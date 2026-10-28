
import os
import re
import json
import html
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
import feedparser
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator


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

TELEGRAM_CHANNELS = [
    ("منوتو", "ManotoTV"),
    ("PMTV News", "Pm_Afshaa"),
]

PAHLAVI_WORDS = [
    "reza pahlavi", "crown prince", "pahlavi",
    "شاهزاده رضا پهلوی", "رضا پهلوی", "پهلوی",
    "شاهزاده", "خاندان پهلوی"
]

IRAN_WORDS = [
    "iran", "iranian", "tehran", "persian",
    "ایران", "ایرانی", "تهران", "جمهوری اسلامی"
]



def clean_text(value):
    if not value:
        return ""

    value = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    value = html.unescape(value)

    # حذف آیدی کانال‌های تلگرام، مثل @Pm_Afshaa
    value = re.sub(
        r"(?<!\w)@[A-Za-z][A-Za-z0-9_]{3,}",
        "",
        value
    )

    # مرتب‌سازی فاصله‌ها
    value = re.sub(r"\s+", " ", value).strip()

    # حذف جداکننده‌ها و ایموجی‌های اضافی انتهای خبر
    value = re.sub(r"[\s|•·—–\-]+$", "", value).strip()

    return value


def is_persian(value):
    return bool(re.search(r"[\u0600-\u06FF]", value or ""))


def translate_to_persian(value):
    value = clean_text(value)

    if not value:
        return ""

    # Don't send Persian text to the translator unnecessarily.
    if is_persian(value):
        return value

    # Retry temporary translation failures.
    for attempt in range(3):
        try:
            if attempt:
                time.sleep(4 * attempt)

            result = GoogleTranslator(
                source="auto",
                target="fa"
            ).translate(value)

            if result and result.strip():
                time.sleep(2)
                return result.strip()

        except Exception as exc:
            logging.warning(
                "Translation attempt %s failed: %s",
                attempt + 1, exc
            )

    # IMPORTANT: Never silently publish untranslated English.
    return None


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
    SEEN_FILE.write_text(
        json.dumps(list(seen)[-10000:], ensure_ascii=False, indent=2),
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
        if url and url.startswith("http"):
            if enclosure.get("type", "").startswith("image/"):
                return url

    for field in ("summary", "description"):
        soup = BeautifulSoup(entry.get(field, ""), "html.parser")
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
    if any(word in lower for word in [
        "war", "attack", "missile", "جنگ", "حمله", "موشک"
    ]):
        return "🚨"
    if any(word in lower for word in [
        "economy", "market", "business", "اقتصاد", "بازار"
    ]):
        return "📊"
    if any(word in lower for word in [
        "election", "president", "انتخابات", "رئیس‌جمهور"
    ]):
        return "🗳️"

    return "🌍"


def priority_for(text, published):
    lower = text.lower()
    age = datetime.now(timezone.utc) - published

    if age <= timedelta(days=7):
        if any(word in lower for word in PAHLAVI_WORDS):
            return 100
        if any(word in lower for word in IRAN_WORDS):
            return 50

    return 10


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
                    entry.get("summary", "")
                    or entry.get("description", "")
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

            logging.info(
                "%s: collected %s entries",
                source, len(parsed.entries)
            )

        except Exception as exc:
            logging.warning("RSS failed (%s): %s", source, exc)

    return news


def collect_telegram():
    news = []

    for source, channel in TELEGRAM_CHANNELS:
        try:
            response = requests.get(
                f"https://t.me/s/{channel}",
                headers=HEADERS,
                timeout=20
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            for post in soup.select(".tgme_widget_message_wrap"):
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
                                published = published.replace(
                                    tzinfo=timezone.utc
                                )
                        except Exception:
                            pass

                image_url = None
                photo = post.select_one(".tgme_widget_message_photo_wrap")

                if photo:
                    match = re.search(
                        r"""background-image:\s*url\(['"]?(.*?)['"]?\)""",
                        photo.get("style", "")
                    )
                    if match:
                        image_url = match.group(1)

                news.append({
                    "id": link,
                    "source": source,
                    "title": text[:180],
                    "summary": text,
                    "link": link,
                    "image": image_url,
                    "published": published,
                    "priority": priority_for(text, published),
                })

        except Exception as exc:
            logging.warning(
                "Telegram channel failed (%s): %s", channel, exc
            )

    return news


def send_text(text):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=30
    )
    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError("Telegram could not send the message")


def send_photo(image_url, caption):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": CHAT_ID,
            "photo": image_url,
            "caption": caption[:1024],
        },
        timeout=40
    )
    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError("Telegram could not send the photo")


def format_news(item):
    title = translate_to_persian(item["title"])

    if not title:
        logging.warning(
            "Skipping story: title translation failed (%s)",
            item["source"]
        )
        return None

    summary = item["summary"][:1200]

    if summary:
        translated_summary = translate_to_persian(summary)

        if not translated_summary:
            logging.warning(
                "Skipping story: summary translation failed (%s)",
                item["source"]
            )
            return None
    else:
        translated_summary = ""

    if translated_summary and translated_summary.startswith(title):
        body = translated_summary
    elif translated_summary:
        body = f"{title}\n\n{translated_summary}"
    else:
        body = title

    emoji = category_for(item["title"] + " " + item["summary"])
    message = f"{emoji} {body}\n\n📰 منبع: {item['source']}"

    # Keep photo captions within Telegram's caption limit.
    return message[:1000]


def main():
    seen = load_seen()

    candidates = collect_rss()
    candidates.extend(collect_telegram())

    unique = {}
    for item in candidates:
        if item["id"] not in seen:
            unique[item["id"]] = item

    candidates = list(unique.values())

    if not candidates:
        logging.info("No new stories found.")
        save_seen(seen)
        return

    candidates.sort(
        key=lambda item: (
            item["priority"],
            item["published"].timestamp()
        ),
        reverse=True
    )

    published_count = 0
    translation_failures = 0

    # Try several stories if translation fails for the first one.
    for item in candidates:
        if published_count >= MAX_NEWS_PER_RUN:
            break

        if translation_failures >= 3:
            logging.warning(
                "Too many translation failures; ending this run."
            )
            break

        message = format_news(item)

        if not message:
            translation_failures += 1
            continue

        try:
            if item.get("image"):
                try:
                    send_photo(item["image"], message)
                except Exception as exc:
                    logging.warning(
                        "Photo failed; trying text instead: %s", exc
                    )
                    send_text(message)
            else:
                send_text(message)

            seen.add(item["id"])
            published_count += 1
            logging.info("Published: %s", item["title"][:100])

        except Exception as exc:
            logging.error("Could not publish story: %s", exc)

    save_seen(seen)


if __name__ == "__main__":
    main()
