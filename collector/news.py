# collector/news.py
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
import requests
from loguru import logger

from config import (
    COINS, CRITICAL_KEYWORDS, BULLISH_KEYWORDS, BEARISH_KEYWORDS,
    COIN_NAME_MAP, RSS_FEEDS, NEWS_POLL_INTERVAL_SEC,
)


def detect_critical_keywords(title: str) -> bool:
    """Return True jika judul mengandung kata kritis (hard gate)."""
    t = title.lower()
    return any(kw in t for kw in CRITICAL_KEYWORDS)


def classify_sentiment(title: str) -> str:
    """Classify berita sebagai bullish/bearish/neutral dari judul."""
    t    = title.lower()
    bull = sum(1 for kw in BULLISH_KEYWORDS if kw in t)
    bear = sum(1 for kw in BEARISH_KEYWORDS if kw in t)
    if bull > bear:  return "bullish"
    if bear > bull:  return "bearish"
    return "neutral"


def coins_mentioned(title: str) -> list[str]:
    """Return list of SYMBOL yang disebut dalam judul berita."""
    t = title.lower()
    return [
        symbol
        for symbol, names in COIN_NAME_MAP.items()
        if any(name in t for name in names)
    ]


def fetch_rss_feed(url: str) -> list[dict]:
    """
    Fetch dan parse satu RSS feed.
    Return list of {title, published_at, source} dicts dari 24 jam terakhir.
    """
    try:
        r = requests.get(url, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            logger.warning(f"RSS {url}: HTTP {r.status_code}")
            return []

        root  = ET.fromstring(r.content)
        items = root.findall("./channel/item")
        now   = datetime.utcnow()
        result = []

        for item in items:
            title    = item.findtext("title", "").strip()
            pub_date = item.findtext("pubDate", "")
            source   = url.split("/")[2]   # domain as source

            try:
                published = parsedate_to_datetime(pub_date).replace(tzinfo=None)
            except Exception:
                published = now

            # Skip berita lebih dari 24 jam
            if (now - published).total_seconds() > 86400:
                continue

            result.append({
                "title":        title,
                "published_at": published,
                "source":       source,
            })

        return result

    except ET.ParseError as e:
        logger.error(f"RSS parse error {url}: {e}")
        return []
    except Exception as e:
        logger.error(f"RSS fetch failed {url}: {e}")
        return []


def fetch_all_news() -> dict[str, list[dict]]:
    """
    Fetch semua RSS feeds dan kelompokkan berita per symbol.
    Return: {symbol: [news_item, ...]}
    4 requests total untuk semua 19 coin.
    """
    by_symbol: dict[str, list[dict]] = {sym: [] for sym in COINS}

    for feed_url in RSS_FEEDS:
        items = fetch_rss_feed(feed_url)
        for item in items:
            for symbol in coins_mentioned(item["title"]):
                by_symbol[symbol].append(item)

    return by_symbol


def build_db_item(title: str, published_at: datetime,
                  source: str, symbol: str) -> dict:
    """Buat DB-ready dict dari raw news item."""
    return {
        "id":           hashlib.md5(f"{symbol}{title}".encode()).hexdigest()[:16],
        "title":        title,
        "published_at": published_at,
        "sentiment":    classify_sentiment(title),
        "is_critical":  detect_critical_keywords(title),
        "votes_pos":    0,
        "votes_neg":    0,
        "source":       source,
    }


def calc_news_modifier(symbol: str, db) -> float:
    """Return score modifier -15 to +8 dari sentimen berita 24 jam."""
    news    = db.get_recent_news(symbol, hours=24)
    if not news:
        return 0.0
    bullish = sum(1 for n in news if n["sentiment"] == "bullish")
    bearish = sum(1 for n in news if n["sentiment"] == "bearish")
    if bullish >= 3 and bearish == 0:  return  8.0
    if bullish > bearish * 1.5:        return  4.0
    if bearish >= 3 and bullish == 0:  return -15.0
    if bearish > bullish * 1.5:        return  -8.0
    return 0.0


def get_news_gate(symbol: str, db) -> dict:
    """Return {'blocked': bool, 'reason': str, 'modifier': float}."""
    block = db.is_news_blocked(symbol)
    if block:
        return {"blocked": True, "reason": block["reason"], "modifier": 0.0}
    return {"blocked": False, "reason": "", "modifier": calc_news_modifier(symbol, db)}


async def poll_news_realtime(db, send_fn) -> None:
    """Poll RSS feeds tiap 5 menit. Kirim alert jika berita kritis."""
    import asyncio
    _count = 0
    while True:
        try:
            news_by_symbol = fetch_all_news()
            for symbol, items in news_by_symbol.items():
                for item in items:
                    parsed = build_db_item(
                        item["title"], item["published_at"],
                        item["source"], symbol
                    )
                    db.upsert_coin_news(symbol, [parsed])
                    if parsed["is_critical"] and not db.is_news_blocked(symbol):
                        db.set_news_block(symbol, parsed["title"])
                        from config import NEWS_BLOCK_HOURS
                        send_fn(
                            f"⛔ <b>{symbol} DIBLOKIR</b> — Berita kritis\n"
                            f"<i>{parsed['title']}</i>\n"
                            f"Block {NEWS_BLOCK_HOURS} jam ke depan.\n"
                            f"Ketik <code>/unblock {symbol}</code> untuk buka manual."
                        )
                        logger.warning(f"NEWS BLOCK: {symbol} — {parsed['title']}")
            _count += 1
            if _count % 100 == 0:
                db.cleanup_old_news(days=7)
        except Exception as e:
            logger.error(f"News poll error: {e}")
        await asyncio.sleep(NEWS_POLL_INTERVAL_SEC)
