# collector/social_lunar.py
import os
import requests
from datetime import datetime, timedelta, date as date_type
from loguru import logger

LUNARCRUSH_BASE = "https://lunarcrush.com/api4/public/coins"

COIN_SLUG_MAP = {
    "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana",
    "XRPUSDT": "ripple", "BNBUSDT": "binancecoin", "ADAUSDT": "cardano",
    "AVAXUSDT": "avalanche-2", "LINKUSDT": "chainlink", "DOTUSDT": "polkadot",
    "TONUSDT": "toncoin", "ONDOUSDT": "ondo-finance", "ARBUSDT": "arbitrum",
    "OPUSDT": "optimism", "NEARUSDT": "near", "INJUSDT": "injective-protocol",
    "SUIUSDT": "sui", "APTUSDT": "aptos", "SEIUSDT": "sei-network",
    "POLUSDT": "matic-network",
}


def fetch_lunarcrush(symbol: str) -> dict:
    """Fetch LunarCrush Galaxy Score. Returns {} if no key or error."""
    api_key = os.getenv("LUNARCRUSH_API_KEY")
    if not api_key:
        logger.debug("LUNARCRUSH_API_KEY not set — skipping")
        return {}

    slug = COIN_SLUG_MAP.get(symbol)
    if not slug:
        return {}

    try:
        resp = requests.get(
            f"{LUNARCRUSH_BASE}/{slug}/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        if not data:
            return {}
        return {
            "symbol": symbol,
            "galaxy_score": float(data.get("galaxy_score", 50)),
            "alt_rank": int(data.get("alt_rank", 50)),
            "social_volume": float(data.get("social_volume_24h", 0)),
        }
    except Exception as e:
        logger.debug(f"LunarCrush fetch failed for {symbol}: {e}")
        return {}


def get_lunarcrush_score(symbol: str, db) -> float:
    """S4: Galaxy Score 0-100 from DB."""
    row = db.get_latest_lunarcrush(symbol)
    if not row:
        return 50.0
    galaxy = float(row.get("galaxy_score") or 50)
    return max(0.0, min(100.0, galaxy))


def fetch_google_trends(symbol: str) -> dict:
    """Fetch Google Trends search interest. Returns {} on failure."""
    try:
        from pytrends.request import TrendReq
        import time
        slug_map = {
            "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana",
            "XRPUSDT": "xrp ripple", "BNBUSDT": "binance coin", "ADAUSDT": "cardano",
        }
        kw = slug_map.get(symbol, symbol.replace("USDT", "").lower())
        pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
        pytrends.build_payload([kw], timeframe="now 7-d")
        df = pytrends.interest_over_time()
        if df.empty or kw not in df.columns:
            return {}
        interest = int(df[kw].iloc[-1])
        time.sleep(1)
        return {
            "symbol": symbol,
            "date": datetime.utcnow().date(),
            "interest": interest,
        }
    except Exception as e:
        logger.debug(f"Google Trends fetch failed for {symbol}: {e}")
        return {}


def get_google_trends_score(symbol: str, db) -> float:
    """S5: Google search interest 0-100 from DB."""
    row = db.get_latest_google_trends(symbol)
    if not row:
        return 50.0
    interest = row.get("interest")
    if interest is None:
        return 50.0
    return float(max(0, min(100, interest)))


def fetch_reddit_sentiment(symbol: str) -> dict:
    """Fetch Reddit post sentiment using PRAW + VADER. Returns {} if no key."""
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "APEX/1.0")

    if not client_id or not client_secret:
        logger.debug("REDDIT_CLIENT_ID/SECRET not set — skipping Reddit sentiment")
        return {}

    try:
        import praw
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )
        analyzer = SentimentIntensityAnalyzer()

        coin_name_map = {
            "BTCUSDT": ["bitcoin", "btc"],
            "ETHUSDT": ["ethereum", "eth"],
            "SOLUSDT": ["solana", "sol"],
            "XRPUSDT": ["xrp", "ripple"],
        }
        keywords = coin_name_map.get(symbol, [symbol.replace("USDT", "").lower()])
        subreddits = ["CryptoCurrency", "Bitcoin", "ethereum", "altcoin"]

        scores = []
        post_count = 0

        for sub_name in subreddits[:2]:
            try:
                sub = reddit.subreddit(sub_name)
                for post in sub.hot(limit=25):
                    title_lower = post.title.lower()
                    if any(kw in title_lower for kw in keywords):
                        vs = analyzer.polarity_scores(post.title)
                        scores.append(vs["compound"])
                        post_count += 1
            except Exception:
                continue

        if not scores:
            return {}

        avg_sentiment = sum(scores) / len(scores)
        bullish_pct = sum(1 for s in scores if s > 0.1) / len(scores) * 100

        return {
            "symbol": symbol,
            "date": datetime.utcnow().date(),
            "post_count": post_count,
            "avg_sentiment": avg_sentiment,
            "bullish_pct": bullish_pct,
        }
    except Exception as e:
        logger.debug(f"Reddit sentiment fetch failed for {symbol}: {e}")
        return {}


def get_reddit_sentiment_score(symbol: str, db) -> float:
    """S6: Reddit sentiment 0-100 from VADER compound + bullish_pct."""
    row = db.get_latest_reddit_sentiment(symbol)
    if not row:
        return 50.0
    avg_sent = float(row.get("avg_sentiment") or 0)
    bullish_pct = float(row.get("bullish_pct") or 50)

    sent_score = (avg_sent + 1) / 2 * 100
    combined = sent_score * 0.6 + bullish_pct * 0.4
    return max(0.0, min(100.0, combined))


def collect_all_social_lunar(db) -> dict:
    """Collect LunarCrush, Google Trends, Reddit for supported coins."""
    from config import COINS
    results = {"lunarcrush": 0, "trends": 0, "reddit": 0}

    for symbol in list(COINS.keys())[:10]:
        data = fetch_lunarcrush(symbol)
        if data:
            try:
                db.upsert_lunarcrush(symbol, {"timestamp": datetime.utcnow(), **data})
                results["lunarcrush"] += 1
            except Exception as e:
                logger.warning(f"Failed to upsert lunarcrush for {symbol}: {e}")

    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]:
        data = fetch_reddit_sentiment(symbol)
        if data:
            try:
                db.upsert_reddit_sentiment(symbol, data)
                results["reddit"] += 1
            except Exception as e:
                logger.warning(f"Failed to upsert reddit sentiment for {symbol}: {e}")

    logger.info(f"Social collected: LunarCrush={results['lunarcrush']}, Reddit={results['reddit']}")
    return results
