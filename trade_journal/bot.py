# trade_journal/bot.py
import asyncio
import os
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

ALLOWED_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", "0"))


async def poll_telegram_commands(db) -> None:
    """
    Long-poll Telegram dan dispatch command ke handler.
    Berjalan selamanya sebagai asyncio coroutine.
    """
    from utils.telegram import get_updates, send

    offset = 0
    loop   = asyncio.get_event_loop()

    while True:
        try:
            updates = await loop.run_in_executor(
                None, lambda o=offset: get_updates(o)
            )
            for update in updates:
                offset  = update["update_id"] + 1
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))

                if chat_id != ALLOWED_CHAT_ID:
                    continue

                text = message.get("text", "").strip()
                if text.startswith("/"):
                    try:
                        await handle_command(text, db, send)
                    except Exception as e:
                        logger.error(f"Command error '{text}': {e}")
                        send(f"❌ Error: {e}")

        except Exception as e:
            logger.error(f"Bot polling error: {e}")

        await asyncio.sleep(2)


async def handle_command(text: str, db, send_fn) -> None:
    """Parse teks command dan dispatch ke handler yang sesuai."""
    parts = text.split()
    cmd   = parts[0].lower().split("@")[0]

    if cmd == "/open":
        if len(parts) < 4:
            send_fn("❓ Format: <code>/open SYMBOL ENTRY STOP</code>\n"
                    "Contoh: <code>/open SOLUSDT 142.34 128.10</code>")
            return
        try:
            await cmd_open(parts[1].upper(), float(parts[2]),
                           float(parts[3]), db, send_fn)
        except ValueError:
            send_fn("❓ Entry dan Stop harus angka.")

    elif cmd == "/close":
        if len(parts) < 3:
            send_fn("❓ Format: <code>/close SYMBOL tp1|stop|HARGA</code>\n"
                    "Contoh: <code>/close SOLUSDT tp1</code>")
            return
        await cmd_close(parts[1].upper(), parts[2], db, send_fn)

    elif cmd == "/status":
        await cmd_status(db, send_fn)

    elif cmd == "/report":
        await cmd_report(db, send_fn)

    elif cmd == "/unblock":
        if len(parts) < 2:
            send_fn("❓ Format: <code>/unblock SYMBOL</code>\n"
                    "Contoh: <code>/unblock SOLUSDT</code>")
            return
        await cmd_unblock(parts[1].upper(), db, send_fn)

    elif cmd in ("/paper", "/paper-report", "/paperreport"):
        await cmd_paper_report(db, send_fn)

    elif cmd in ("/help", "/start"):
        send_fn(
            "🤖 <b>APEX Bot — Command yang tersedia:</b>\n\n"
            "<b>📊 Laporan</b>\n"
            "<code>/status</code> — lihat paper sim yang masih open\n"
            "<code>/paper</code> — laporan Bot Trading History Simulation\n"
            "<code>/report</code> — laporan mingguan trade journal\n\n"
            "<b>✍️ Trade Journal (manual)</b>\n"
            "<code>/open SYMBOL ENTRY STOP</code>\n"
            "  contoh: <code>/open SOLUSDT 142.34 128.10</code>\n"
            "<code>/close SYMBOL tp1|stop|HARGA</code>\n"
            "  contoh: <code>/close SOLUSDT tp1</code>\n\n"
            "<b>🔧 Lainnya</b>\n"
            "<code>/unblock SYMBOL</code> — lepas news block\n"
            "<code>/help</code> — tampilkan pesan ini"
        )

    else:
        send_fn(
            "❓ Command tidak dikenal. Ketik <code>/help</code> untuk daftar command."
        )


async def cmd_open(symbol: str, entry: float, stop: float,
                   db, send_fn) -> None:
    """Handle /open — catat trade baru."""
    from trade_journal.journal import calc_tp_prices, format_open_msg
    from risk.manager import get_portfolio_usd

    tp1, tp2 = calc_tp_prices(entry, stop)

    sig          = db.get_last_signal_for_symbol(symbol, within_hours=48)
    signal_score = sig["total_score"] if sig else None
    signal_id    = sig["signal_id"]   if sig else None

    trade_id = db.open_journal_trade(
        symbol=symbol, entry_price=entry, stop_price=stop,
        tp1_price=tp1, tp2_price=tp2,
        signal_score=signal_score, signal_id=signal_id,
    )

    msg = format_open_msg(
        symbol=symbol, entry=entry, stop=stop, tp1=tp1, tp2=tp2,
        signal_score=signal_score, signal_linked=signal_id is not None,
    )
    send_fn(msg)
    logger.info(f"Journal: opened {symbol} @ {entry} (id={trade_id})")


async def cmd_close(symbol: str, exit_arg: str, db, send_fn) -> None:
    """Handle /close — catat exit trade."""
    from trade_journal.journal import calc_pnl, format_close_msg
    from risk.manager import get_portfolio_usd

    trade = db.get_open_journal_trade_by_symbol(symbol)
    if not trade:
        send_fn(f"❓ Tidak ada posisi terbuka untuk <b>{symbol}</b>.")
        return

    if exit_arg.lower() == "tp1":
        exit_price, exit_reason = trade["tp1_price"], "TP1"
    elif exit_arg.lower() == "stop":
        exit_price, exit_reason = trade["stop_price"], "Stop"
    else:
        try:
            exit_price, exit_reason = float(exit_arg), "Manual"
        except ValueError:
            send_fn("❓ Gunakan: tp1, stop, atau angka harga.")
            return

    portfolio_usd = get_portfolio_usd()
    pnl = calc_pnl(
        symbol=symbol,
        entry_price=trade["entry_price"],
        exit_price=exit_price,
        stop_price=trade["stop_price"],
        portfolio_usd=portfolio_usd,
    )

    open_dt = trade["open_time"]
    if hasattr(open_dt, "to_pydatetime"):
        open_dt = open_dt.to_pydatetime()
    if open_dt.tzinfo:
        open_dt = open_dt.replace(tzinfo=None)
    hold_hours = (datetime.utcnow() - open_dt).total_seconds() / 3600

    db.close_journal_trade(
        trade_id=trade["id"], exit_price=exit_price,
        exit_reason=exit_reason.lower(),
        pnl_usd=pnl["pnl_usd"], pnl_idr=pnl["pnl_idr"],
        r_multiple=pnl["r_multiple"],
    )

    msg = format_close_msg(
        symbol=symbol, exit_price=exit_price, exit_reason=exit_reason,
        pnl_usd=pnl["pnl_usd"], pnl_idr=pnl["pnl_idr"],
        r_multiple=pnl["r_multiple"], hold_hours=hold_hours,
    )
    send_fn(msg)
    logger.info(f"Journal: closed {symbol} @ {exit_price} | P&L: ${pnl['pnl_usd']:.2f}")


async def cmd_status(db, send_fn) -> None:
    """Handle /status — tampilkan posisi terbuka."""
    trades = db.get_open_journal_trades()

    if not trades:
        send_fn("📋 Tidak ada posisi terbuka saat ini.")
        return

    lines = [f"📋 <b>Posisi Terbuka ({len(trades)})</b>", "─" * 30]
    for t in trades:
        current = db.get_latest_price(t["symbol"])
        if current:
            pnl_pct = (current - t["entry_price"]) / t["entry_price"] * 100
            sign    = "+" if pnl_pct >= 0 else ""
            lines.append(
                f"<b>{t['symbol']}</b>  Entry ${t['entry_price']:.4f} | "
                f"Harga ${current:.4f}\n"
                f"  {sign}{pnl_pct:.1f}% | TP1: ${t['tp1_price']:.4f}"
            )
        else:
            lines.append(
                f"<b>{t['symbol']}</b>  Entry ${t['entry_price']:.4f} | "
                f"TP1: ${t['tp1_price']:.4f}"
            )

    send_fn("\n".join(lines))


async def cmd_unblock(symbol: str, db, send_fn) -> None:
    """Handle /unblock — lepas news block manual."""
    block = db.is_news_blocked(symbol)
    if not block:
        send_fn(f"ℹ️ {symbol} tidak sedang diblokir.")
        return
    db.clear_news_block(symbol)
    send_fn(
        f"✅ <b>{symbol} news block dilepas</b>\n"
        f"Alasan sebelumnya: <i>{block['reason']}</i>\n"
        f"Sinyal kembali aktif."
    )
    logger.info(f"Manual unblock: {symbol}")


async def cmd_paper_report(db, send_fn) -> None:
    """Handle /paper — kirim Bot Trading History Simulation."""
    from utils.telegram import send_paper_report
    history = db.get_paper_sim_history(days=60)
    send_paper_report(history)


async def cmd_report(db, send_fn) -> None:
    """Handle /report — trigger laporan mingguan manual."""
    from datetime import timedelta
    from trade_journal.journal import (generate_weekly_report,
                                       detect_performance_gap, format_gap_alert)

    date_to   = datetime.utcnow().date()
    date_from = date_to - timedelta(days=7)

    trades_df = db.get_journal_trades_by_period(str(date_from), str(date_to))
    report    = generate_weekly_report(trades_df, str(date_from), str(date_to))
    send_fn(report)

    date_2w   = date_to - timedelta(days=14)
    trades_2w = db.get_journal_trades_by_period(str(date_2w), str(date_to))
    gap       = detect_performance_gap(trades_2w, db)
    if gap:
        send_fn(format_gap_alert(gap))
