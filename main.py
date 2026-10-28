
import os
import re
import html
import requests
import feedparser
from deep_translator import GoogleTranslator

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

FEEDS = [
    "https://feeds.bbci.co.uk/persian/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
]

translator = GoogleTranslator(source="auto", target="fa")


def translate(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text).strip()
    if not text:
        return ""
    try:
        return translator.translate(text[:4000]) or text
    except Exception:
        return text


def category(text):
    rules = {
        "⚔️ جنگ و امنیت": [
            "war", "attack", "missile", "military", "iran",
            "جنگ", "حمله", "موشک", "ارتش", "امنیت"
        ],
        "🏛 سیاست": [
            "politics", "president", "election", "government",
            "سیاست", "رئیس‌جمهور", "انتخابات", "دولت"
        ],
        "💰 اقتصاد": [
            "economy", "market", "oil", "trade", "اقتصاد",
            "بازار", "نفت", "تجارت", "تورم"
        ],
        "💻 فناوری": [
            "technology", "artificial intelligence", "AI",
            "تکنولوژی", "فناوری", "هوش مصنوعی"
        ],
    }

    text = text.lower()
    for name, words in rules.items():
        if any(word in text for word in words):
            return name
    return "🌍 اخبار جهان"


def send_message(text):
    url = (
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    )
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
        raise RuntimeError("Telegram rejected the message")


def main():
    news = []

    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:10]:
                link = entry.get("link", "").strip()
                title = entry.get("title", "").strip()

                if link and title:
                    news.append((title, link, entry))
        except Exception as error:
            print("Feed error:", error)

    # Avoid duplicates within this single run.
    unique = {}
    for title, link, entry in news:
        unique.setdefault(link, (title, link, entry))

    for title, link, entry in list(unique.values())[:4]:
        original = title + " " + entry.get("summary", "")
        translated_title = translate(title)
        summary = translate(entry.get("summary", ""))

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
            print("Published:", link)
        except Exception as error:
            print("Publish error:", error)


if __name__ == "__main__":
    main()
