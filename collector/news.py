# collector/news.py
import os
import hashlib
from datetime import datetime, timedelta
import requests
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("CRYPTOPANIC_API_KEY", "")
BASE_URL = "https://cryptopanic.com/api/v1/posts/"

from config import COINS, CRITICAL_KEYWORDS, NEWS_POLL_INTERVAL_SEC


def detect_critical_keywords(title: str) -> bool:
    """Return True jika judul berita mengandung kata kritis."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in CRITICAL_KEYWORDS)


def fetch_news_batch() -> list:
    """
    Fetch berita terbaru untuk semua coin dalam satu request.
    Graceful degradation: return [] jika API key tidak ada.
    """
    if not API_KEY:
        logger.debug("CRYPTOPANIC_API_KEY tidak diset — news dilewati")
        return []
    currencies = ",".join(sym.replace("USDT", "") for sym in COINS.keys())
    try:
        r = requests.get(BASE_URL, params={
            "auth_token": API_KEY,
            "currencies": currencies,
            "filter":     "hot",
            "kind":       "news",
            "public":     "true",
        }, timeout=15)
        return r.json().get("results", [])
    except Exception as e:
        logger.error(f"CryptoPanic fetch failed: {e}")
        return []


def parse_news_item(item: dict, symbol: str) -> dict:
    """Normalize CryptoPanic item ke format DB."""
    title  = item.get("title", "")
    votes  = item.get("votes", {})
    pos    = votes.get("positive", 0)
    neg    = votes.get("negative", 0)

    if pos > neg * 1.5:
        sentiment = "bullish"
    elif neg > pos * 1.5:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    return {
        "id":           hashlib.md5(f"{symbol}{title}".encode()).hexdigest()[:16],
        "title":        title,
        "sentiment":    sentiment,
        "is_critical":  detect_critical_keywords(title),
        "votes_pos":    pos,
        "votes_neg":    neg,
        "source":       item.get("source", {}).get("domain", ""),
        "published_at": item.get("published_at", datetime.utcnow().isoformat()),
    }


def calc_news_modifier(symbol: str, db) -> float:
    """Return score modifier -15 to +8 dari sentimen berita 24 jam."""
    news     = db.get_recent_news(symbol, hours=24)
    if not news:
        return 0.0
    bullish  = sum(1 for n in news if n["sentiment"] == "bullish")
    bearish  = sum(1 for n in news if n["sentiment"] == "bearish")
    if bullish >= 3 and bearish == 0:  return  8.0
    if bullish > bearish * 1.5:        return  4.0
    if bearish >= 3 and bullish == 0:  return -15.0
    if bearish > bullish * 1.5:        return -8.0
    return 0.0


def get_news_gate(symbol: str, db) -> dict:
    """Return {'blocked': bool, 'reason': str, 'modifier': float}."""
    block = db.is_news_blocked(symbol)
    if block:
        return {"blocked": True, "reason": block["reason"], "modifier": 0.0}
    return {"blocked": False, "reason": "", "modifier": calc_news_modifier(symbol, db)}


async def poll_news_realtime(db, send_fn) -> None:
    """Poll CryptoPanic tiap 5 menit. Kirim alert jika berita kritis."""
    import asyncio
    _count = 0
    while True:
        try:
            raw_items = fetch_news_batch()
            for item in raw_items:
                for currency_info in item.get("currencies", []):
                    code   = currency_info.get("code", "").upper()
                    symbol = code + "USDT"
                    if symbol not in COINS:
                        continue
                    parsed = parse_news_item(item, symbol)
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
