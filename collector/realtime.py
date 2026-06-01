# ============================================================
# collector/realtime.py — Binance WebSocket Real-Time Stream
# ============================================================
# Streams: 4H kline data for all 19 coins simultaneously
# One connection handles all coins (Binance combined streams)
# Auto-reconnects on disconnect
# Triggers signal engine on every confirmed candle close
# ============================================================

import asyncio
import json
import websockets
import pandas as pd
from datetime import datetime
from loguru import logger

from config import COINS, TIMEFRAMES
from database import get_db


# Binance combined stream URL
WS_BASE = "wss://stream.binance.com:9443/stream?streams="

# Callbacks registered by other modules
_on_candle_close_callbacks = []

def on_candle_close(func):
    """Decorator to register a callback for confirmed candle close."""
    _on_candle_close_callbacks.append(func)
    return func


def build_stream_url() -> str:
    """Build Binance combined stream URL for all coins × timeframes."""
    streams = []
    for symbol in COINS.keys():
        sym_lower = symbol.lower()
        # Primary: 4H candles (signal engine)
        streams.append(f"{sym_lower}@kline_4h")
        # Daily for structure
        streams.append(f"{sym_lower}@kline_1d")

    url = WS_BASE + "/".join(streams)
    logger.debug(f"WebSocket streams: {len(streams)} total")
    return url


def parse_kline_message(msg: dict) -> dict | None:
    """
    Parse a Binance kline WebSocket message.
    Returns candle dict if candle is CLOSED, else None.
    """
    try:
        data  = msg.get("data", msg)
        kline = data.get("k", {})

        if not kline.get("x", False):  # x = is candle closed?
            return None                # Ignore open (in-progress) candles

        return {
            "symbol":    kline["s"],           # e.g. "BTCUSDT"
            "timeframe": kline["i"],           # e.g. "4h"
            "timestamp": pd.Timestamp(kline["t"], unit="ms"),
            "open":      float(kline["o"]),
            "high":      float(kline["h"]),
            "low":       float(kline["l"]),
            "close":     float(kline["c"]),
            "volume":    float(kline["v"]),
            "closed":    True,
        }
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"Failed to parse kline message: {e}")
        return None


async def handle_message(raw: str, db):
    """Process one WebSocket message."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    candle = parse_kline_message(msg)
    if candle is None:
        return  # In-progress candle, skip

    # Store in database
    df = pd.DataFrame([candle])
    db.upsert_candles(candle["symbol"], candle["timeframe"], df)

    symbol = candle["symbol"]
    tf     = candle["timeframe"]
    close  = candle["close"]

    logger.debug(f"✓ Candle closed: {symbol} {tf} | close={close:.4f}")

    # Fire callbacks (signal engine, etc.)
    for callback in _on_candle_close_callbacks:
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(candle)
            else:
                callback(candle)
        except Exception as e:
            logger.error(f"Callback error for {callback.__name__}: {e}")


async def connect_and_stream():
    """Main WebSocket loop with auto-reconnect."""
    db  = get_db()
    url = build_stream_url()

    reconnect_delay = 5   # seconds
    max_delay       = 60

    while True:
        try:
            logger.info(f"Connecting to Binance WebSocket...")
            logger.info(f"Monitoring {len(COINS)} coins × 2 timeframes = "
                        f"{len(COINS)*2} streams")

            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=2**20  # 1MB max message size
            ) as ws:
                reconnect_delay = 5  # Reset on successful connect
                logger.info("✓ WebSocket connected — live data flowing")
                logger.info("  Waiting for 4H candle closes to fire signal engine...")

                async for raw in ws:
                    await handle_message(raw, db)

        except websockets.ConnectionClosed as e:
            logger.warning(f"WebSocket closed: {e}. Reconnecting in {reconnect_delay}s...")

        except Exception as e:
            logger.error(f"WebSocket error: {e}. Reconnecting in {reconnect_delay}s...")

        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, max_delay)


def start_realtime():
    """Entry point — runs the WebSocket stream."""
    asyncio.run(connect_and_stream())


if __name__ == "__main__":
    start_realtime()
