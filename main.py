
import os
import re
import json
import html
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from statistics import mean, median
from zoneinfo import ZoneInfo

import requests
import feedparser
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEEN_FILE = Path("seen.json")
TETHER_STATE_FILE = Path("tether_state.json")
MAX_NEWS_PER_RUN = 1
TEHRAN = ZoneInfo("Asia/Tehran")
TETHER_ALERT_THRESHOLD_TOMAN = 5000
TETHER_REPORT_HOURS = {0, 12}

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
    "reza pahlavi", "crown prince reza pahlavi", "pahlavi",
    "شاهزاده رضا پهلوی", "رضا پهلوی", "پهلوی", "خاندان پهلوی"
]

FREEDOM_WORDS = [
    "protest", "protests", "demonstration", "demonstrations",
    "revolution", "freedom movement", "political prisoner",
    "political prisoners", "execution", "executions", "crackdown",
    "strike", "strikes", "opposition", "regime change",
    "اعتراض", "اعتراضات", "انقلاب", "آزادی‌خواهی", "آزادی خواهی",
    "زندانی سیاسی", "اعدام", "اعتصاب", "سرکوب",
    "گذار از جمهوری اسلامی"
]

IRAN_WORDS = [
    "iran", "iranian", "tehran", "persian",
    "ایران", "ایرانی", "تهران", "جمهوری اسلامی"
]

IRAN_EVENT_WORDS = [
    "war", "attack", "missile", "sanctions", "nuclear",
    "negotiations", "inflation", "prices", "currency",
    "rial", "toman", "oil", "protest", "protests",
    "execution", "prisoner", "crackdown", "internet shutdown",
    "strike", "human rights", "power outage",
    "جنگ", "حمله", "موشک", "تحریم", "هسته‌ای", "مذاکرات",
    "تورم", "گرانی", "قیمت", "ارز", "ریال", "تومان", "نفت",
    "اعتراض", "اعدام", "زندانی", "سرکوب", "قطع اینترنت",
    "اعتصاب", "حقوق بشر", "قطعی برق", "کمبود آب"
]

WORLD_EVENT_WORDS = [
    "war", "invasion", "ceasefire", "missile", "airstrike",
    "attack", "election", "resigns", "resignation", "coup",
    "earthquake", "tsunami", "wildfire", "flood", "hurricane",
    "nuclear", "sanctions", "recession", "inflation",
    "central bank", "oil prices", "nato",
    "un security council", "hostages", "terrorist attack",
    "جنگ", "حمله", "آتش‌بس", "انتخابات", "استعفا", "کودتا",
    "زلزله", "سیل", "آتش‌سوزی", "تحریم", "رکود اقتصادی", "گروگان"
]

ROUTINE_STATEMENT_WORDS = [
    "says", "said", "claims", "claimed", "insists",
    "announces", "according to", "اظهار کرد", "گفت",
    "اعلام کرد", "مدعی شد", "ادعا کرد", "تأکید کرد"
]


def clean_text(value):
    if not value:
        return ""

    value = BeautifulSoup(
        str(value), "html.parser"
    ).get_text(" ", strip=True)

    value = html.unescape(value)

    # حذف آیدی کانال تلگرام
    value = re.sub(
        r"(?<!\w)@[A-Za-z][A-Za-z0-9_]{3,}",
        "",
        value
    )

    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"[\s|•·—–\-]+$", "", value).strip()

    # حذف امضای خنده در انتهای پست
    value = re.sub(r"\s*😁\s*$", "", value).strip()

    return value


def is_persian(value):
    return bool(re.search(r"[\u0600-\u06FF]", value or ""))


def contains_any(text, words):
    text = (text or "").lower()
    return any(word in text for word in words)


def translate_to_persian(value):
    value = clean_text(value)

    if not value:
        return ""

    if is_persian(value):
        return value

    for attempt in range(2):
        try:
            if attempt:
                time.sleep(3)

            result = GoogleTranslator(
                source="auto",
                target="fa"
            ).translate(value)

            if result and result.strip():
                return clean_text(result)

        except Exception as exc:
            logging.warning(
                "Translation attempt %s failed: %s",
                attempt + 1, exc
            )

    # در صورت شکست ترجمه، خبر انگلیسی منتشر نمی‌شود.
    return None


def parse_date(entry):
    parsed = (
        entry.get("published_parsed")
        or entry.get("updated_parsed")
    )

    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass

    return datetime.now(timezone.utc)


def is_important_news(text, published):
    age = datetime.now(timezone.utc) - published

    if age > timedelta(hours=72):
        return False

    if age < timedelta(minutes=-10):
        return False

    # اخبار رضا پهلوی و آزادی‌خواهی اولویت دارند.
    if contains_any(text, PAHLAVI_WORDS):
        return True

    if contains_any(text, FREEDOM_WORDS):
        return True

    iran_related = contains_any(text, IRAN_WORDS)
    iran_event = contains_any(text, IRAN_EVENT_WORDS)

    # اظهارات عادی مقامات بدون رویداد مهم منتشر نشوند.
    if (
        iran_related
        and contains_any(text, ROUTINE_STATEMENT_WORDS)
        and not iran_event
    ):
        return False

    if iran_related and iran_event:
        return True

    return contains_any(text, WORLD_EVENT_WORDS)


def category_for(text):
    if contains_any(text, PAHLAVI_WORDS):
        return "👑🦁"

    if contains_any(text, FREEDOM_WORDS):
        return "👑🦁"

    if contains_any(text, IRAN_WORDS):
        return "🇮🇷"

    return "🌍"


def priority_for(text, published):
    if not is_important_news(text, published):
        return 0

    age_hours = max(
        0,
        (datetime.now(timezone.utc) - published).total_seconds() / 3600
    )

    freshness = max(0, 72 - int(age_hours))

    if contains_any(text, PAHLAVI_WORDS):
        return 300 + freshness

    if contains_any(text, FREEDOM_WORDS):
        return 250 + freshness

    if (
        contains_any(text, IRAN_WORDS)
        and contains_any(text, IRAN_EVENT_WORDS)
    ):
        return 200 + freshness

    return 100 + freshness


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

        if (
            url
            and url.startswith("http")
            and enclosure.get("type", "").startswith("image/")
        ):
            return url

    for field in ("summary", "description"):
        soup = BeautifulSoup(
            entry.get(field, ""), "html.parser"
        )

        image = soup.find("img")

        if image:
            url = image.get("src") or image.get("data-src")

            if url and url.startswith("http"):
                return url

    return None


def collect_rss():
    news = []

    for source, feed_url in FEEDS:
        try:
            response = requests.get(
                feed_url,
                headers=HEADERS,
                timeout=20
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
                priority = priority_for(combined, published)

                if priority <= 0:
                    continue

                news.append({
                    "id": link,
                    "source": source,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "image": extract_feed_image(entry),
                    "published": published,
                    "priority": priority,
                })

            logging.info(
                "%s: collected %s entries",
                source, len(parsed.entries)
            )

        except Exception as exc:
            logging.warning(
                "RSS failed (%s): %s",
                source, exc
            )

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
                message = post.select_one(
                    ".tgme_widget_message_text"
                )

                if not message:
                    continue

                text = clean_text(
                    message.get_text(" ", strip=True)
                )

                if len(text) < 20:
                    continue

                date_link = post.select_one(
                    "a.tgme_widget_message_date"
                )

                link = (
                    date_link.get("href", "")
                    if date_link else ""
                )

                if not link:
                    data_post = post.select_one("[data-post]")

                    if data_post:
                        link = (
                            "https://t.me/"
                            + data_post.get("data-post", "")
                        )

                if not link:
                    continue

                published = datetime.now(timezone.utc)

                if date_link:
                    time_element = date_link.select_one("time")

                    if (
                        time_element
                        and time_element.get("datetime")
                    ):
                        try:
                            published = datetime.fromisoformat(
                                time_element["datetime"].replace(
                                    "Z", "+00:00"
                                )
                            )

                            if published.tzinfo is None:
                                published = published.replace(
                                    tzinfo=timezone.utc
                                )

                        except Exception:
                            pass

                priority = priority_for(text, published)

                if priority <= 0:
                    continue

                image_url = None
                photo = post.select_one(
                    ".tgme_widget_message_photo_wrap"
                )

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
                    "priority": priority,
                })

        except Exception as exc:
            logging.warning(
                "Telegram channel failed (%s): %s",
                channel, exc
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
    raw_title = clean_text(item["title"])
    raw_summary = clean_text(item.get("summary", ""))

    # حذف تکرار تیتر در ابتدای پست‌های تلگرامی
    if raw_summary.startswith(raw_title):
        raw_summary = raw_summary[len(raw_title):].lstrip(
            " :-—–|"
        )

    title = translate_to_persian(raw_title)

    if not title:
        logging.warning(
            "Skipping story: title translation failed (%s)",
            item["source"]
        )
        return None

    summary = ""

    if raw_summary and raw_summary.lower() != raw_title.lower():
        translated = translate_to_persian(raw_summary[:700])

        if translated:
            summary = re.sub(
                r"\s+", " ", translated
            ).strip()

    title = re.sub(r"\s+", " ", title).strip()
    title = title[:150].rstrip()

    summary = summary[:210].rstrip()

    if summary.lower().startswith(title.lower()):
        summary = summary[len(title):].lstrip(" :—–-")

    emoji = category_for(raw_title + " " + raw_summary)

    body = f"{emoji} {title}"

    if summary:
        body += f"\n{summary}"

    return f"{body}\n\n📰 منبع: {item['source']}"[:900]


def load_seen():
    try:
        if SEEN_FILE.exists():
            data = json.loads(
                SEEN_FILE.read_text(encoding="utf-8")
            )

            if isinstance(data, list):
                return set(str(item) for item in data)

    except Exception as exc:
        logging.warning("Could not read seen.json: %s", exc)

    return set()


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(
            list(seen)[-10000:],
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def load_tether_state():
    try:
        if TETHER_STATE_FILE.exists():
            data = json.loads(
                TETHER_STATE_FILE.read_text(encoding="utf-8")
            )

            if isinstance(data, dict):
                data.setdefault("history", [])
                data.setdefault("last_report_slot", "")
                data.setdefault("alert_active", False)
                return data

    except Exception as exc:
        logging.warning(
            "Could not read tether_state.json: %s", exc
        )

    return {
        "history": [],
        "last_report_slot": "",
        "alert_active": False
    }


def save_tether_state(state):
    TETHER_STATE_FILE.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def fetch_tether_prices():
    prices = {}

    # نوبیتکس: بازار USDT/IRT با واحد ریال
    try:
        response = requests.get(
            "https://apiv2.nobitex.ir/v3/orderbook/USDTIRT",
            headers=HEADERS,
            timeout=15
        )
        response.raise_for_status()

        data = response.json()

        if data.get("status") == "ok":
            raw = data.get("lastTradePrice")

            if (
                not raw
                and data.get("asks")
                and data.get("bids")
            ):
                raw = (
                    float(data["asks"][0][0])
                    + float(data["bids"][0][0])
                ) / 2

            value = float(raw) / 10 if raw else 0

            if value > 0:
                prices["نوبیتکس"] = value

    except Exception as exc:
        logging.warning(
            "Nobitex price unavailable: %s", exc
        )

    # والکس: تلاش برای دریافت بازار USDT/IRT یا USDT/IRR
    try:
        response = requests.get(
            "https://api.wallex.ir/v1/markets",
            headers=HEADERS,
            timeout=15
        )
        response.raise_for_status()

        data = response.json()
        result = data.get("result", {})
        markets = (
            result.get("markets", result)
            if isinstance(result, dict)
            else result
        )

        if isinstance(markets, dict):
            market_items = markets.values()
        elif isinstance(markets, list):
            market_items = markets
        else:
            market_items = []

        for market in market_items:
            if not isinstance(market, dict):
                continue

            symbol = str(
                market.get("symbol", market.get("market", ""))
            ).upper()

            base = str(
                market.get("baseAsset", "")
            ).upper()

            quote = str(
                market.get("quoteAsset", "")
            ).upper()

            compact_symbol = symbol.replace("-", "").replace("_", "")

            is_tether_irt = (
                compact_symbol in {"USDTIRT", "USDTIRR"}
                or (
                    base == "USDT"
                    and quote in {"IRT", "IRR"}
                )
            )

            if not is_tether_irt:
                continue

            stats = market.get("stats", {}) or {}

            raw = (
                stats.get("lastPrice")
                or market.get("lastPrice")
                or market.get("price")
            )

            if not raw:
                continue

            value = float(raw)

            if (
                quote in {"IRT", "IRR"}
                or compact_symbol.endswith(("IRT", "IRR"))
            ):
                value /= 10

            if value > 0:
                prices["والکس"] = value
                break

    except Exception as exc:
        logging.warning(
            "Wallex price unavailable: %s", exc
        )

    return prices


def format_toman(value):
    digits = f"{int(round(value)):,}"

    return digits.translate(
        str.maketrans(
            "0123456789",
            "۰۱۲۳۴۵۶۷۸۹"
        )
    )


def tether_price_message(prices, average_price, change=None):
    if len(prices) > 1:
        source_line = (
            f"میانگین {len(prices)} صرافی: "
            + "، ".join(prices.keys())
        )
    else:
        source_line = "منبع: " + next(iter(prices.keys()))

    message = (
        "💵 قیمت تتر (USDT)\n"
        f"💰 {format_toman(average_price)} تومان\n"
        f"{source_line}"
    )

    if change is not None:
        sign = "+" if change >= 0 else "−"

        message += (
            f"\n📊 تغییر ۲۴ ساعته: {sign}"
            f"{format_toman(abs(change))} تومان"
        )

    return message


def check_tether_price():
    state = load_tether_state()
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(TEHRAN)

    prices = fetch_tether_prices()

    if not prices:
        logging.warning(
            "No tether prices available from supported exchanges."
        )
        return

    values = list(prices.values())

    if len(values) >= 3:
        center = median(values)

        filtered = {
            name: price
            for name, price in prices.items()
            if abs(price - center) / center <= 0.05
        }

        if filtered:
            prices = filtered
            values = list(prices.values())

    current_price = mean(values)

    history = []

    for item in state.get("history", []):
        try:
            timestamp = datetime.fromisoformat(
                item["timestamp"]
            )

            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(
                    tzinfo=timezone.utc
                )

            if now - timestamp <= timedelta(hours=30):
                history.append({
                    "timestamp": timestamp.isoformat(),
                    "price": float(item["price"])
                })

        except Exception:
            continue

    reference = None

    for item in history:
        timestamp = datetime.fromisoformat(
            item["timestamp"]
        )

        age = now - timestamp

        if timedelta(hours=23) <= age <= timedelta(hours=25):
            distance = abs(
                age.total_seconds() - 24 * 3600
            )

            if reference is None or distance < reference[0]:
                reference = (distance, item["price"])

    change_24h = (
        current_price - reference[1]
        if reference else None
    )

    history.append({
        "timestamp": now.isoformat(),
        "price": round(current_price, 2)
    })

    state["history"] = history[-220:]

    # یک هشدار برای هر جهش پیوسته؛ بعد از افت، دوباره آماده هشدار می‌شود.
    if change_24h is not None:
        above_threshold = (
            change_24h >= TETHER_ALERT_THRESHOLD_TOMAN
        )

        if above_threshold and not state.get("alert_active", False):
            send_text(
                "🚨 هشدار افزایش قیمت تتر\n"
                + tether_price_message(
                    prices, current_price, change_24h
                )
                + "\n⚠️ افزایش بیش از ۵٬۰۰۰ تومان نسبت به حدود ۲۴ ساعت قبل."
            )

            state["alert_active"] = True

        elif not above_threshold:
            state["alert_active"] = False

    # اولین اجرای هر بازه ده‌دقیقه‌ای در ساعت ۰۰ یا ۱۲ تهران
    if (
        local_now.hour in TETHER_REPORT_HOURS
        and local_now.minute < 10
    ):
        slot = (
            f"{local_now.date().isoformat()}-"
            f"{local_now.hour:02d}"
        )

        if state.get("last_report_slot") != slot:
            send_text(
                tether_price_message(
                    prices, current_price, change_24h
                )
            )

            state["last_report_slot"] = slot

    save_tether_state(state)


def main():
    # بررسی قیمت تتر در هر اجرا
    try:
        check_tether_price()
    except Exception:
        logging.exception("Tether check failed")

    seen = load_seen()

    candidates = collect_rss() + collect_telegram()

    unique = {}

    for item in candidates:
        if item["id"] not in seen:
            unique[item["id"]] = item

    candidates = [
        item for item in unique.values()
        if item.get("priority", 0) > 0
    ]

    if not candidates:
        logging.info("No new important stories found.")
        save_seen(seen)
        save_tether_state(load_tether_state())
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
                        "Photo failed; trying text instead: %s",
                        exc
                    )
                    send_text(message)
            else:
                send_text(message)

            seen.add(item["id"])
            published_count += 1

            logging.info(
                "Published: %s",
                item["title"][:100]
            )

        except Exception as exc:
            logging.error(
                "Could not publish story: %s", exc
            )

    save_seen(seen)
    save_tether_state(load_tether_state())


if __name__ == "__main__":
    main()
