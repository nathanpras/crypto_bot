# trade_journal/reminder.py
from datetime import datetime
from loguru import logger


async def check_tp_sl_reminders(candle: dict, db, send_fn) -> None:
    """
    Dipanggil setiap candle close.
    Kirim reminder Telegram jika TP1/SL tercapai atau trade terlalu lama.
    Anti-spam: 4 jam cooldown per trade.
    """
    symbol = candle.get("symbol", "")
    high   = candle.get("high", 0.0)
    low    = candle.get("low", float("inf"))

    trades = db.get_open_journal_trades_by_symbol(symbol)
    if not trades:
        return

    now = datetime.utcnow()

    for trade in trades:
        trade_id = trade["id"]

        if trade.get("reminder_sent_at"):
            last = trade["reminder_sent_at"]
            if hasattr(last, "to_pydatetime"):
                last = last.to_pydatetime()
            if last.tzinfo:
                last = last.replace(tzinfo=None)
            if (now - last).total_seconds() < 4 * 3600:
                continue

        reminded = False

        if high >= trade["tp1_price"]:
            send_fn(
                f"🎯 <b>{symbol} nyentuh TP1</b> "
                f"(${trade['tp1_price']:.4f})\n"
                f"Sudah close?\n"
                f"<code>/close {symbol} tp1</code>"
            )
            reminded = True

        elif low <= trade["stop_price"]:
            send_fn(
                f"🛑 <b>{symbol} kena stop</b> "
                f"(${trade['stop_price']:.4f})\n"
                f"Sudah close?\n"
                f"<code>/close {symbol} stop</code>"
            )
            reminded = True

        else:
            open_time = trade["open_time"]
            if hasattr(open_time, "to_pydatetime"):
                open_time = open_time.to_pydatetime()
            if open_time.tzinfo:
                open_time = open_time.replace(tzinfo=None)
            days_open = (now - open_time).days
            if days_open >= 7:
                send_fn(
                    f"⏰ <b>{symbol} sudah {days_open} hari terbuka</b>\n"
                    f"Mau di-close?\n"
                    f"<code>/close {symbol} [harga]</code>"
                )
                reminded = True

        if reminded:
            db.update_journal_reminder_sent(trade_id)
            logger.debug(f"Reminder sent for {symbol} trade {trade_id}")
