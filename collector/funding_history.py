# collector/funding_history.py
import os
import requests
from datetime import datetime
from loguru import logger

COINALYZE_BASE = "https://api.coinalyze.net/v1/funding-rate-history"

COINALYZE_SYMBOL_MAP = {
    "BTCUSDT": "BTCUSDT_PERP.A", "ETHUSDT": "ETHUSDT_PERP.A",
    "SOLUSDT": "SOLUSDT_PERP.A", "XRPUSDT": "XRPUSDT_PERP.A",
    "BNBUSDT": "BNBUSDT_PERP.A",
}


def fetch_funding_history(symbol: str, days: int = 30) -> list:
    """Fetch funding rate history from Coinalyze. Returns [] if no key."""
    api_key = os.getenv("COINALYZE_API_KEY")
    if not api_key:
        logger.debug("COINALYZE_API_KEY not set — skipping funding history")
        return []

    coinalyze_sym = COINALYZE_SYMBOL_MAP.get(symbol)
    if not coinalyze_sym:
        return []

    try:
        from_ts = int((datetime.utcnow().timestamp() - days * 86400) * 1000)
        resp = requests.get(
            COINALYZE_BASE,
            params={"symbols": coinalyze_sym, "from": from_ts, "interval": "8h"},
            headers={"api_key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data or not isinstance(data, list):
            return []

        records = []
        for item in data[0].get("history", []):
            records.append({
                "symbol": symbol,
                "timestamp": datetime.utcfromtimestamp(item["t"] / 1000),
                "funding_rate": float(item.get("o", 0)),
            })
        return records
    except Exception as e:
        logger.debug(f"Coinalyze fetch failed for {symbol}: {e}")
        return []


def get_funding_oscillator_score(symbol: str, db) -> float:
    """
    T10: Funding rate oscillator — current funding vs 30d moving average.
    Current << MA (negative funding while usually positive) = bullish.
    Current >> MA (very high) = overleveraged longs = bearish.
    Returns 0-100.
    """
    history = db.get_funding_history(symbol, limit=720)
    if len(history) < 10:
        return 50.0

    current = float(history[0]["funding_rate"] or 0)
    ma_30d = sum(float(r["funding_rate"] or 0) for r in history) / len(history)

    deviation = current - ma_30d

    if deviation < -0.005:    return 82.0
    elif deviation < -0.002:  return 70.0
    elif deviation < 0:       return 58.0
    elif deviation < 0.002:   return 50.0
    elif deviation < 0.005:   return 40.0
    elif deviation < 0.010:   return 28.0
    else:                      return 15.0


def collect_all_funding_history(db) -> int:
    """Collect 30d funding history for major perps. Returns count stored."""
    count = 0
    for symbol in COINALYZE_SYMBOL_MAP:
        records = fetch_funding_history(symbol)
        for rec in records:
            try:
                db.upsert_funding_history(symbol, rec)
            except Exception as e:
                logger.warning(f"Failed to upsert funding history for {symbol}: {e}")
        if records:
            count += 1
            logger.info(f"Funding history: {symbol} — {len(records)} records")
    return count
