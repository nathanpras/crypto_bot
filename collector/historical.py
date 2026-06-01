# ============================================================
# collector/historical.py — Fetch Historical OHLCV Data
# ============================================================

import ccxt
import pandas as pd
from datetime import datetime, timedelta
from loguru import logger
import time

from config import COINS, TIMEFRAMES, HISTORICAL_PERIODS
from database import get_db


def get_exchange():
    exchange = ccxt.bybit({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    return exchange


def fetch_ohlcv_full(symbol: str, timeframe: str,
                     days: int, exchange) -> pd.DataFrame:
    """
    Fetch full historical OHLCV data going back `days` days.
    Handles pagination automatically (Binance limit = 1000 candles per request).
    """
    since_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    all_candles = []
    page = 0

    while True:
        try:
            candles = exchange.fetch_ohlcv(
                symbol, timeframe, since=since_ms, limit=1000
            )
            if not candles:
                break

            all_candles.extend(candles)
            page += 1

            last_ts = candles[-1][0]
            since_ms = last_ts + 1

            # Check if we reached present
            tf_ms = {
                "1h": 3_600_000, "4h": 14_400_000,
                "1d": 86_400_000, "1w": 604_800_000
            }.get(timeframe, 14_400_000)

            if len(candles) < 1000:
                break
            if since_ms > datetime.utcnow().timestamp() * 1000:
                break

            # Rate limit courtesy
            time.sleep(exchange.rateLimit / 1000)

        except ccxt.NetworkError as e:
            logger.warning(f"Network error fetching {symbol} {timeframe}: {e}. Retrying...")
            time.sleep(5)
            continue
        except Exception as e:
            logger.error(f"Error fetching {symbol} {timeframe}: {e}")
            break

    if not all_candles:
        logger.warning(f"No data returned for {symbol} {timeframe}")
        return pd.DataFrame()

    df = pd.DataFrame(all_candles,
                      columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)

    logger.debug(f"  {symbol} {timeframe}: {len(df)} candles "
                 f"({df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()})")
    return df


def fetch_all_historical():
    """
    Download historical data for all coins and timeframes.
    Shows progress. Takes ~5–10 minutes total.
    """
    db = get_db()
    exchange = get_exchange()

    total = len(COINS) * len(TIMEFRAMES)
    done  = 0

    logger.info("=" * 55)
    logger.info("APEX — Fetching historical data (Phase 1)")
    logger.info(f"Coins: {len(COINS)} | Timeframes: {len(TIMEFRAMES)}")
    logger.info("=" * 55)

    for symbol, info in COINS.items():
        logger.info(f"\n[{info['tier']}] {symbol} — {info['name']}")

        for tf_name, tf_code in TIMEFRAMES.items():
            days = HISTORICAL_PERIODS.get(tf_code, 365)

            # Skip if already have recent data
            latest = db.get_latest_timestamp(symbol, tf_code)
            if latest:
                age_hours = (datetime.utcnow() - latest).total_seconds() / 3600
                tf_hours  = {"1h":1,"4h":4,"1d":24,"1w":168}.get(tf_code,4)
                if age_hours < tf_hours * 2:
                    logger.debug(f"  {tf_code}: up to date ({latest}), skipping")
                    done += 1
                    continue

            df = fetch_ohlcv_full(symbol, tf_code, days, exchange)
            if not df.empty:
                db.upsert_candles(symbol, tf_code, df)

            done += 1
            pct = done / total * 100
            logger.info(f"  ✓ {tf_code} ({tf_name}): {len(df)} candles — [{done}/{total}] {pct:.0f}%")

            # Rate limit: be kind to Binance
            time.sleep(0.3)

    logger.info("\n" + "=" * 55)
    logger.info("✓ Historical data fetch complete!")
    logger.info("=" * 55)


def fetch_incremental(symbol: str, timeframe: str, exchange=None):
    """
    Fetch only missing candles since last stored timestamp.
    Used for real-time updates between WebSocket reconnects.
    """
    if exchange is None:
        exchange = get_exchange()

    db = get_db()
    latest = db.get_latest_timestamp(symbol, timeframe)

    if latest is None:
        days = HISTORICAL_PERIODS.get(timeframe, 365)
        df = fetch_ohlcv_full(symbol, timeframe, days, exchange)
    else:
        since_ms = int(latest.timestamp() * 1000)
        candles = exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=500)
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles,
                          columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        # Remove the last candle (might be incomplete)
        df = df.iloc[:-1]

    if not df.empty:
        db.upsert_candles(symbol, timeframe, df)

    return df


if __name__ == "__main__":
    fetch_all_historical()
