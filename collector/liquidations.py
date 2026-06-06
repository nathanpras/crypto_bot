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


def get_liquidation_score_bybit(symbol: str) -> float:
    """
    O5: Liquidation cascade score using Bybit OI delta estimate (no API key).
    Uses OI drop + price direction as proxy for liquidation pressure.
    """
    import requests
    try:
        # Get 2 hours of OI data
        r_oi = requests.get(
            "https://api.bytick.com/v5/market/open-interest",
            params={"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 3},
            timeout=10,
        )
        oi_items = r_oi.json().get("result", {}).get("list", [])

        # Get current price + 24h change
        r_tick = requests.get(
            "https://api.bytick.com/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=10,
        )
        tick_items = r_tick.json().get("result", {}).get("list", [])

        if len(oi_items) < 2 or not tick_items:
            return 50.0

        oi_now = float(oi_items[0]["openInterest"])
        oi_prev = float(oi_items[1]["openInterest"])
        price_chg = float(tick_items[0].get("price24hPcnt", 0) or 0)
        last_price = float(tick_items[0].get("lastPrice", 0) or 0)

        oi_delta_pct = (oi_now - oi_prev) / oi_prev * 100 if oi_prev > 0 else 0
        oi_usd = oi_now * last_price

        # Large OI drop = forced liquidations
        if oi_delta_pct < -3.0:
            if price_chg < 0:
                # Long liquidation cascade (capitulation) = contrarian bullish
                liq_usd = abs(oi_delta_pct) / 100 * oi_usd
                if liq_usd > 50_000_000:  return 82.0
                elif liq_usd > 10_000_000: return 70.0
                else:                       return 62.0
            else:
                # Short squeeze (shorts liquidated) = momentum bullish
                return 68.0
        elif oi_delta_pct < -1.0:
            return 58.0  # Mild deleveraging
        elif oi_delta_pct > 3.0 and price_chg > 0:
            return 42.0  # OI building + price up = leveraged longs (risky)
        else:
            return 50.0  # No significant liquidation pressure

    except Exception:
        return 50.0


def collect_all_liquidations(db) -> int:
    """Collect liquidations for all major coins. Returns count of successes."""
    from config import COINS
    count = 0
    for symbol in COINS:
        data = fetch_liquidation_cascade(symbol)
        if data:
            try:
                db.upsert_liquidation(symbol, {
                    "liq_long_usd": data["liq_long_24h"],
                    "liq_short_usd": data["liq_short_24h"],
                    "timestamp": data["timestamp"],
                })
                count += 1
            except Exception as e:
                logger.warning(f"Failed to upsert liquidation for {symbol}: {e}")
    logger.info(f"Liquidations collected: {count}/{len(COINS)} symbols")
    return count
