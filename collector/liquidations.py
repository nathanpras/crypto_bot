# collector/liquidations.py
import os
import requests
from datetime import datetime
from loguru import logger

COINGLASS_API = "https://open-api.coinglass.com/api/pro/v3/futures/liquidation-history"


def fetch_liquidation_cascade(symbol: str) -> dict:
    """Fetch 24h liquidation totals from CoinGlass. Returns {} if key missing or error."""
    api_key = os.getenv("COINGLASS_API_KEY")
    if not api_key:
        logger.debug("COINGLASS_API_KEY not set — skipping liquidations")
        return {}

    coin = symbol.replace("USDT", "").replace("USDC", "")
    try:
        resp = requests.get(
            COINGLASS_API,
            params={"symbol": coin, "interval": "1h", "limit": 24},
            headers={"coinglassSecret": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if str(data.get("code")) != "0":
            logger.debug(f"CoinGlass error for {symbol}: {data.get('msg')}")
            return {}

        items = data.get("data", [])
        if not items:
            return {}

        total_long = sum(float(i.get("longLiqUsd", 0)) for i in items)
        total_short = sum(float(i.get("shortLiqUsd", 0)) for i in items)

        return {
            "symbol": symbol,
            "liq_long_24h": total_long,
            "liq_short_24h": total_short,
            "timestamp": datetime.utcnow(),
        }
    except Exception as e:
        logger.debug(f"CoinGlass fetch failed for {symbol}: {e}")
        return {}


def get_liquidation_cascade_score(symbol: str, db) -> float:
    """
    Score 0-100 from liquidation imbalance.
    Short liq >> long liq (short squeeze) = bullish = high score.
    Long liq >> short liq (long wipeout) = bearish = low score.
    """
    row = db.get_latest_liquidation(symbol)
    if not row:
        return 50.0

    long_usd = float(row.get("liq_long_usd") or 0)
    short_usd = float(row.get("liq_short_usd") or 0)
    total = long_usd + short_usd

    if total < 500_000:
        return 50.0

    short_ratio = short_usd / total
    return max(0.0, min(100.0, short_ratio * 100))


def collect_all_liquidations(db) -> int:
    """Collect liquidations for all major coins. Returns count of successes."""
    from config import COINS
    count = 0
    for symbol in COINS:
        data = fetch_liquidation_cascade(symbol)
        if data:
            db.upsert_liquidation(symbol, {
                "liq_long_usd": data["liq_long_24h"],
                "liq_short_usd": data["liq_short_24h"],
                "timestamp": data["timestamp"],
            })
            count += 1
    logger.info(f"Liquidations collected: {count}/{len(COINS)} symbols")
    return count
