# ============================================================
# collector/realtime.py — Bybit WebSocket Real-Time Stream
# ============================================================
# Streams: 4H + 1D kline data for all 19 coins simultaneously
# Bybit WebSocket v5 public spot endpoint
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


WS_URL = "wss://stream.bybit.com/v5/public/spot"

# Bybit interval → standard timeframe mapping
BYBIT_TF_MAP = {"240": "4h", "D": "1d"}

# Callbacks registered by other modules
_on_candle_close_callbacks = []

def on_candle_close(func):
    """Decorator to register a callback for confirmed candle close."""
    _on_candle_close_callbacks.append(func)
    return func


def build_subscribe_args() -> list:
    """Build Bybit subscription topics for all coins × timeframes."""
    args = []
    for symbol in COINS.keys():
        args.append(f"kline.240.{symbol}")   # 4H
        args.append(f"kline.D.{symbol}")     # 1D
    return args


def parse_bybit_message(msg: dict) -> dict | None:
    """
    Parse a Bybit v5 kline WebSocket message.
    Returns candle dict only when candle is CONFIRMED (closed), else None.
    """
    try:
        topic = msg.get("topic", "")
        if not topic.startswith("kline."):
            return None

        data = msg.get("data", [])
        if not data:
            return None

        kline = data[0]

        # Only process confirmed (closed) candles
        if not kline.get("confirm", False):
            return None

        # Parse topic: "kline.240.BTCUSDT" or "kline.D.BTCUSDT"
        parts     = topic.split(".")
        interval  = parts[1]
        symbol    = parts[2]
        timeframe = BYBIT_TF_MAP.get(interval, interval)

        return {
            "symbol":    symbol,
            "timeframe": timeframe,
            "timestamp": pd.Timestamp(int(kline["start"]), unit="ms"),
            "open":      float(kline["open"]),
            "high":      float(kline["high"]),
            "low":       float(kline["low"]),
            "close":     float(kline["close"]),
            "volume":    float(kline["volume"]),
            "closed":    True,
        }
    except (KeyError, TypeError, ValueError) as e:
        logger.warning(f"Failed to parse Bybit message: {e}")
        return None


async def handle_message(raw: str, db):
    """Process one WebSocket message."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    # Respond to server ping
    if msg.get("op") == "ping":
        return  # websockets library handles pong automatically

    candle = parse_bybit_message(msg)
    if candle is None:
        return  # In-progress candle or non-kline message

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

    # Phase 3: TP/SL reminder check
    try:
        from trade_journal.reminder import check_tp_sl_reminders
        from utils.telegram import send
        await check_tp_sl_reminders(candle, db, send)
    except Exception as e:
        logger.error(f"Reminder check error: {e}")


async def connect_and_stream():
    """Main Bybit WebSocket loop with auto-reconnect."""
    db   = get_db()
    args = build_subscribe_args()

    reconnect_delay = 5
    max_delay       = 60

    while True:
        try:
            logger.info("Connecting to Bybit WebSocket...")
            logger.info(f"Monitoring {len(COINS)} coins × 2 timeframes = {len(args)} streams")

            async with websockets.connect(
                WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=2**20,
            ) as ws:
                reconnect_delay = 5

                # Subscribe in batches of 10 (Bybit limit per message)
                for i in range(0, len(args), 10):
                    batch = args[i:i+10]
                    await ws.send(json.dumps({"op": "subscribe", "args": batch}))

                logger.info("✓ Bybit WebSocket connected — live data flowing")
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
