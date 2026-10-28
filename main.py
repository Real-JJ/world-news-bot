import os
import re
import html
import json
import requests
import feedparser
from pathlib import Path
from deep_translator import GoogleTranslator

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEEN_FILE = Path("seen.json")
MAX_NEWS_PER_RUN = 4

FEEDS = [
    "https://feeds.bbci.co.uk/persian/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
]

translator = GoogleTranslator(source="auto", target="fa")


def clean_text(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def translate(text):
    text = clean_text(text)

    if not text:
        return ""

    # Avoid retranslating text that already contains Persian.
    if re.search(r"[\u0600-\u06FF]", text):
        return text

    try:
        return translator.translate(text[:4000]) or text
    except Exception as error:
        print("Translation error:", error)
        return text


def category(text):
    text = text.lower()

    rules = {
        "⚔️ جنگ و امنیت": [
            "war", "attack", "missile", "military",
            "جنگ", "حمله", "موشک", "ارتش", "امنیت"
        ],
        "🏛 سیاست": [
            "politics", "president", "election", "government",
            "سیاست", "رئیس‌جمهور", "انتخابات", "دولت"
        ],
        "💰 اقتصاد": [
            "economy", "market", "oil", "trade",
            "اقتصاد", "بازار", "نفت", "تجارت", "تورم"
        ],
        "💻 فناوری": [
            "technology", "artificial intelligence",
            "software", "فناوری", "تکنولوژی", "هوش مصنوعی"
        ],
    }

    for name, words in rules.items():
        if any(word in text for word in words):
            return name

    return "🌍 اخبار جهان"


def load_seen():
    if not SEEN_FILE.exists():
        return []

    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_seen(seen):
    # Keep the most recent 2000 links.
    SEEN_FILE.write_text(
        json.dumps(seen[-2000:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")


def main():
    seen = load_seen()
    seen_set = set(seen)
    candidates = []

    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)

            if getattr(feed, "bozo", False):
                print("Feed warning:", feed_url)

            for entry in feed.entries:
                link = entry.get("link", "").strip()
                title = clean_text(entry.get("title", ""))

                if link and title and link not in seen_set:
                    candidates.append((title, link, entry))
        except Exception as error:
            print("Feed error:", feed_url, error)

    # Remove duplicate links from the current batch.
    unique = {}
    for title, link, entry in candidates:
        unique.setdefault(link, (title, link, entry))

    published = 0

    for title, link, entry in list(unique.values())[:MAX_NEWS_PER_RUN]:
        original_summary = clean_text(entry.get("summary", ""))
        original = f"{title} {original_summary}"

        translated_title = translate(title)
        summary = translate(original_summary)

        if len(summary) > 650:
            summary = summary[:647].rsplit(" ", 1)[0] + "..."

        message = (
            f"{category(original)}\n\n"
            f"📰 {translated_title}\n\n"
            f"{summary}\n\n"
            f"🔗 منبع و گزارش کامل:\n{link}\n\n"
            f"#اخبار_جهان"
        )

        try:
            send_message(message)
            seen.append(link)
            seen_set.add(link)
            published += 1
            print("Published:", link)
        except Exception as error:
            print("Publish error:", error)

    save_seen(seen)
    print(f"Finished. Published {published} new stories.")


if __name__ == "__main__":
    main()
