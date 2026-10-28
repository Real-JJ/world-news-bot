
import os
import re
import json
import html
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import requests
import feedparser
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator


# =========================
# Configuration
# =========================

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEEN_FILE = Path("seen.json")
MAX_NEWS_PER_RUN = 1

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WorldNewsBot/1.0)"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

# RSS news sources
FEEDS = [
    ("ایران اینترنشنال", "https://www.iranintl.com/feed"),
    ("رادیو فردا", "https://www.radiofarda.com/api/zpoqil-vomx-tpe_kip"),
    ("الجزیره", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("گاردین", "https://www.theguardian.com/world/rss"),
    ("گاردین اقتصاد", "https://www.theguardian.com/business/rss"),
    ("فرانس ۲۴", "https://www.france24.com/en/rss"),
    ("یورونیوز", "https://www.euronews.com/rss"),
    ("اسکای نیوز", "https://feeds.skynews.com/feeds/rss/world.xml"),
    ("نیویورک تایمز", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
]

# Public Telegram channels
TELEGRAM_CHANNELS = [
    ("منوتو", "ManotoTV"),
    ("PMTV News", "Pm_Afshaa"),
]

IRAN_WORDS = [
    "iran", "iranian", "tehran", "persian",
    "ایران", "ایرانی", "تهران", "جمهوری اسلامی"
]

PAHLAVI_WORDS = [
    "reza pahlavi", "crown prince", "pahlavi",
    "شاهزاده رضا پهلوی", "رضا پهلوی", "پهلوی",
    "شاهزاده", "خاندان پهلوی"
]

TRANSLATOR = GoogleTranslator(source="auto", target="fa")


# =========================
# Helpers
# =========================

def clean_text(value):
    if not value:
        return ""
    value = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def translate_to_persian(value):
    value = clean_text(value)
    if not value:
        return ""

    # Keep text that already contains Persian characters.
    if re.search(r"[\u0600-\u06FF]", value):
        return value

    try:
        return TRANSLATOR.translate(value) or value
    except Exception as exc:
        logging.warning("Translation failed: %s", exc)
        return value


def load_seen():
    try:
        if SEEN_FILE.exists():
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(str(item) for item in data)
    except Exception as exc:
        logging.warning("Could not read seen.json: %s", exc)

    return set()


def save_seen(seen):
    # Keep the history from growing without limit.
    items = list(seen)[-10000:]
    SEEN_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def parse_date(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")

    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass

    return datetime.now(timezone.utc)


def extract_feed_image(entry):
    for field in ("media_content", "media_thumbnail"):
        items = entry.get(field, [])
        if isinstance(items, dict):
            items = [items]

        for item in items:
            url = item.get("url")
            if url and url.startswith("http"):
                return url

    for enclosure in entry.get("enclosures", []):
        url = enclosure.get("href") or enclosure.get("url")
        kind = enclosure.get("type", "")
        if url and (kind.startswith("image/") or not kind):
            if url.startswith("http"):
                return url

    # Some feeds put the image inside their summary HTML.
    for field in ("summary", "description"):
        raw = entry.get(field, "")
        soup = BeautifulSoup(raw, "html.parser")
        image = soup.find("img")
        if image:
            url = image.get("src") or image.get("data-src")
            if url and url.startswith("http"):
                return url

    return None


def category_for(text):
    lower = text.lower()

    if any(word in lower for word in PAHLAVI_WORDS):
        return "👑"
    if any(word in lower for word in IRAN_WORDS):
        return "🇮🇷"
    if any(word in lower for word in ["war", "attack", "missile", "جنگ", "حمله", "موشک"]):
        return "🚨"
    if any(word in lower for word in ["economy", "market", "business", "اقتصاد", "بازار"]):
        return "📊"
    if any(word in lower for word in ["election", "president", "انتخابات", "رئیس‌جمهور"]):
        return "🗳️"

    return "🌍"


def priority_for(text, published):
    lower = text.lower()
    age = datetime.now(timezone.utc) - published

    # Old stories should not jump ahead just because they mention Pahlavi.
    recent = age <= timedelta(days=7)

    if recent and any(word in lower for word in PAHLAVI_WORDS):
        return 100
    if recent and any(word in lower for word in IRAN_WORDS):
        return 50

    return 10


# =========================
# RSS collection
# =========================

def collect_rss():
    news = []

    for source, feed_url in FEEDS:
        try:
            response = requests.get(
                feed_url, headers=HEADERS, timeout=20
            )
            response.raise_for_status()

            parsed = feedparser.parse(response.content)

            for entry in parsed.entries[:30]:
                link = entry.get("link", "").strip()
                title = clean_text(entry.get("title", ""))

                if not link or not title:
                    continue

                summary = clean_text(
                    entry.get("summary", "") or entry.get("description", "")
                )

                published = parse_date(entry)
                combined = f"{title} {summary}"

                news.append({
                    "id": link,
                    "source": source,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "image": extract_feed_image(entry),
                    "published": published,
                    "priority": priority_for(combined, published),
                })

            logging.info("%s: collected %s entries", source, len(parsed.entries))

        except Exception as exc:
            logging.warning("RSS failed (%s): %s", source, exc)

    return news


# =========================
# Public Telegram collection
# =========================

def collect_telegram():
    news = []

    for source, channel in TELEGRAM_CHANNELS:
        page_url = f"https://t.me/s/{channel}"

        try:
            response = requests.get(
                page_url, headers=HEADERS, timeout=20
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            posts = soup.select(".tgme_widget_message_wrap")

            for post in posts:
                message = post.select_one(".tgme_widget_message_text")
                if not message:
                    continue

                text = clean_text(message.get_text(" ", strip=True))
                if len(text) < 20:
                    continue

                date_link = post.select_one("a.tgme_widget_message_date")
                link = date_link.get("href", "") if date_link else ""

                if not link:
                    data_post = post.select_one("[data-post]")
                    if data_post:
                        link = "https://t.me/" + data_post.get("data-post", "")

                if not link:
                    continue

                published = datetime.now(timezone.utc)
                if date_link:
                    time_element = date_link.select_one("time")
                    if time_element and time_element.get("datetime"):
                        try:
                            published = datetime.fromisoformat(
                                time_element["datetime"].replace("Z", "+00:00")
                            )
                            if published.tzinfo is None:
                                published = published.replace(tzinfo=timezone.utc)
                        except Exception:
                            pass

                image_url = None
                photo = post.select_one(".tgme_widget_message_photo_wrap")

                if photo:
                    style = photo.get("style", "")
                    match = re.search(
                        r"""background-image:\s*url\(['"]?(.*?)['"]?\)""",
                        style
                    )
                    if match:
                        image_url = match.group(1)

                combined = text

                news.append({
                    "id": link,
                    "source": source,
                    "title": text[:180],
                    "summary": text,
                    "link": link,
                    "image": image_url,
                    "published": published,
                    "priority": priority_for(combined, published),
                })

        except Exception as exc:
            logging.warning("Telegram channel failed (%s): %s", channel, exc)

    return news


# =========================
# Telegram publishing
# =========================

def send_text(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {result}")


def send_photo(image_url, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "photo": image_url,
            "caption": caption[:1024],
        },
        timeout=40,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendPhoto failed: {result}")


def format_news(item):
    original = f"{item['title']} {item['summary']}".strip()

    title = translate_to_persian(item["title"])
    summary = translate_to_persian(item["summary"])

    # Avoid repeating the title if the summary already starts with it.
    if summary and summary.startswith(title):
        body = summary
    elif summary:
        body = f"{title}\n\n{summary}"
    else:
        body = title

    emoji = category_for(original)
    source = item["source"]

    # Intentionally do not display the article URL.
    return f"{emoji} {body}\n\n📰 منبع: {source}"


# =========================
# Main
# =========================

def main():
    seen = load_seen()

    candidates = collect_rss()
    candidates.extend(collect_telegram())

    # Remove duplicates and previously published items.
    unique = {}
    for item in candidates:
        if item["id"] in seen:
            continue
        unique[item["id"]] = item

    candidates = list(unique.values())

    if not candidates:
        logging.info("No new stories found.")
        save_seen(seen)
        return

    # Prefer recent Pahlavi-related news, then Iran-related news,
    # while using publication time to select the newest within each tier.
    candidates.sort(
        key=lambda item: (
            item["priority"],
            item["published"].timestamp()
        ),
        reverse=True,
    )

    published_count = 0

    for item in candidates:
        if published_count >= MAX_NEWS_PER_RUN:
            break

        caption = format_news(item)

        try:
            if item.get("image"):
                try:
                    send_photo(item["image"], caption)
                except Exception as exc:
                    logging.warning(
                        "Photo failed; sending text instead: %s", exc
                    )
                    send_text(caption)
            else:
                send_text(caption)

            seen.add(item["id"])
            published_count += 1
            logging.info("Published: %s", item["title"][:100])

        except Exception as exc:
            logging.error("Could not publish story: %s", exc)

    save_seen(seen)


if __name__ == "__main__":
    main()
