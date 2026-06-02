#!/usr/bin/env python3
# ============================================================
# main.py — APEX Trading System Entry Point
# ============================================================
# Usage:
#   python3 main.py --fetch-history    Download 2 years of data
#   python3 main.py --run              Start live scanning
#   python3 main.py --scan-once        Run one scan now
#   python3 main.py --status           Show current signals + portfolio
#   python3 main.py --macro            Check macro gates (F1+F2)
# ============================================================

import sys
import asyncio
import argparse
from datetime import datetime
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

# ── Setup paths ───────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

# ── Logging setup ─────────────────────────────────────────────
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | "
           "<level>{level: <8}</level> | {message}", level="INFO")
logger.add("logs/apex.log", rotation="1 week", level="DEBUG")

Path("logs").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)

# ── Imports ───────────────────────────────────────────────────
from database import get_db
from collector.historical import fetch_all_historical
from collector.macro import fetch_all_macro
from signals.engine import scan_all_coins
from risk.manager import calc_position_size, check_portfolio_guards, format_trade_for_telegram, get_portfolio_usd
from utils.telegram import send_signal_alert, send_system_status, send_daily_report
from config import COINS, SIGNAL_THRESHOLD
from collector.onchain_enhanced import collect_all_onchain
from collector.narrative import collect_all_tvl
from collector.token_unlocks import collect_all_token_unlocks


# ── Scan once ─────────────────────────────────────────────────

def run_scan_once():
    """Run one full scan cycle manually."""
    logger.info("Running manual scan...")

    # Get macro state
    macro = fetch_all_macro()
    f1    = macro["f1_gate"]
    f2    = macro["f2_gate"]

    logger.info(f"F1 (Macro): {f1['reason']}")
    logger.info(f"F2 (Cycle): {f2['label']}")

    if not f1["passed"]:
        logger.warning("F1 GATE FAILED — No trades allowed. Holding stablecoin.")
        return

    # Score all coins
    fear_greed = macro["macro"]["fear_greed"].get("value", 50)
    results = scan_all_coins(
        fear_greed=fear_greed,
        allowed_tiers=f2["allowed_tiers"]
    )

    # Display results
    print("\n" + "═"*55)
    print(f"{'APEX SIGNAL SCAN':^55}")
    print("═"*55)
    print(f"{'COIN':<12} {'SCORE':>6} {'REGIME':<16} {'PRICE':>12} {'STATUS'}")
    print("─"*55)

    for r in results:
        score  = r.get("total_score", 0)
        status = "🌪 STORM" if r.get("strong") else \
                 "🔔 SIGNAL" if r.get("fired") else \
                 "⚫ —"
        print(f"{r['symbol']:<12} {score:>6.1f} "
              f"{r.get('regime','—'):<16} "
              f"{r.get('price',0):>12.4f} {status}")

    fired = [r for r in results if r.get("fired")]
    if fired:
        print("\n" + "─"*55)
        print(f"{'SIGNALS TO REVIEW':^55}")
        print("─"*55)
        portfolio_usd = get_portfolio_usd()
        for signal in fired:
            calc = calc_position_size(
                signal["symbol"],
                signal["price"],
                signal["total_score"],
                portfolio_usd
            )
            guard = check_portfolio_guards(signal["symbol"])

            print(f"\n{'═'*40}")
            print(format_trade_for_telegram(calc, signal))
            if not guard["allowed"]:
                print(f"⛔ BLOCKED: {guard['reason']}")
    else:
        print(f"\n  No signals this scan. Highest: {results[0]['symbol']} "
              f"at {results[0]['total_score']:.1f}/100")

    print("═"*55 + "\n")


# ── Live mode ─────────────────────────────────────────────────

async def live_loop():
    """
    Main live loop:
    - WebSocket streams 24/7
    - Signal engine runs on every 4H candle close
    - Telegram bot polling for journal commands
    - Weekly P&L report + performance gap detector
    """
    from collector.realtime import on_candle_close, connect_and_stream
    from trade_journal.bot import poll_telegram_commands

    logger.info("Starting APEX live mode...")
    send_system_status("started", "Live scanning all coins 24/7")

    db         = get_db()
    macro_data = fetch_all_macro()
    _state     = {
        "macro":             macro_data,
        "last_macro_update": 0,
        "scan_count":        0,
    }

    @on_candle_close
    async def handle_close(candle: dict):
        """Called every time a 4H candle closes for any coin."""
        symbol = candle["symbol"]
        tf     = candle["timeframe"]

        if tf != "4h":
            return

        logger.info(f"4H candle closed: {symbol} @ {candle['close']:.4f}")
        _state["scan_count"] += 1

        if _state["scan_count"] % 144 == 0:
            logger.info("Refreshing macro data...")
            _state["macro"] = fetch_all_macro()

        macro = _state["macro"]
        f1    = macro["f1_gate"]
        f2    = macro["f2_gate"]

        if not f1["passed"]:
            logger.info(f"F1 GATE: {f1['reason']} — skip scan")
            return

        fear_greed = macro["macro"]["fear_greed"].get("value", 50)

        from signals.engine import score_coin
        result = score_coin(symbol, fear_greed=fear_greed,
                            allowed_tiers=f2["allowed_tiers"])

        if result.get("fired"):
            portfolio_usd = get_portfolio_usd()
            calc = calc_position_size(
                symbol, result["price"],
                result["total_score"],
                portfolio_usd
            )

            if not calc["valid"]:
                logger.warning(f"Trade rejected: {calc['reject_reason']}")
                return

            guard = check_portfolio_guards(symbol)
            if not guard["allowed"]:
                logger.warning(f"Portfolio guard: {guard['reason']}")
                return

            msg = format_trade_for_telegram(calc, result)
            send_signal_alert(msg)
            logger.info(f"🔔 SIGNAL SENT: {symbol} | Score: {result['total_score']}")

    await asyncio.gather(
        connect_and_stream(),
        poll_telegram_commands(db),
        _run_weekly_report_scheduler(db),
        _poll_news_loop(db),
    )


async def _poll_news_loop(db) -> None:
    """Wrapper untuk poll_news_realtime — poll berita tiap 5 menit."""
    from collector.news import poll_news_realtime
    from utils.telegram import send
    await poll_news_realtime(db, send)


async def _run_weekly_report_scheduler(db) -> None:
    """Kirim laporan P&L setiap Senin jam 00:00 UTC."""
    from datetime import timedelta
    from trade_journal.journal import (generate_weekly_report,
                                       detect_performance_gap, format_gap_alert)

    while True:
        now        = datetime.utcnow()
        days_ahead = (7 - now.weekday()) % 7 or 7
        next_run   = now.replace(hour=0, minute=0, second=0, microsecond=0) + \
                     timedelta(days=days_ahead)
        wait_secs  = (next_run - now).total_seconds()
        logger.info(f"Weekly report scheduled in {wait_secs/3600:.1f} jam")
        await asyncio.sleep(wait_secs)

        date_to   = datetime.utcnow().date()
        date_from = date_to - timedelta(days=7)

        trades_df = db.get_journal_trades_by_period(str(date_from), str(date_to))
        report    = generate_weekly_report(trades_df, str(date_from), str(date_to))
        send_signal_alert(report)
        logger.info("Weekly report sent")

        date_2w   = date_to - timedelta(days=14)
        trades_2w = db.get_journal_trades_by_period(str(date_2w), str(date_to))
        gap       = detect_performance_gap(trades_2w, db)
        if gap:
            send_signal_alert(format_gap_alert(gap))
            logger.warning("Performance gap detected — alert sent")


# ── Status display ────────────────────────────────────────────

def show_status():
    """Show current system status and latest signals."""
    db = get_db()

    print("\n" + "═"*50)
    print(f"{'APEX SYSTEM STATUS':^50}")
    print("═"*50)

    # Latest signals from DB
    signals = db.get_latest_signals()
    if not signals.empty:
        print("\nLatest signal scores:")
        for _, row in signals.head(10).iterrows():
            status = "🔔" if row.get("fire") else "·"
            print(f"  {status} {row['symbol']:<12} {row['total_score']:>6.1f}/100")

    # Trade stats
    stats = db.get_trade_stats(30)
    print(f"\nLast 30 days: {stats['total']} trades | "
          f"Win rate: {stats['win_rate']}% | "
          f"Avg R: {stats['avg_r']}R")
    print(f"Total P&L: ${stats['total_pnl']:+.2f}")

    # Portfolio
    portfolio_usd = get_portfolio_usd()
    import os
    idr_rate = float(os.getenv("IDR_RATE", 17_800))
    print(f"\nPortfolio: ${portfolio_usd:.2f} | "
          f"Rp {portfolio_usd * idr_rate:,.0f}")

    # Open trades
    open_t = db.get_open_trades()
    print(f"Open positions: {len(open_t)}/{3}")
    if not open_t.empty:
        for _, t in open_t.iterrows():
            print(f"  → {t['symbol']}: entry ${t['entry_price']:.4f} | "
                  f"stop ${t['stop_price']:.4f}")

    print("═"*50 + "\n")


# ── Entry point ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="APEX Trading System")
    parser.add_argument("--fetch-history", action="store_true",
                        help="Download 2 years of historical OHLCV data")
    parser.add_argument("--run",  action="store_true",
                        help="Start live 24/7 scanning mode")
    parser.add_argument("--scan-once", action="store_true",
                        help="Run one scan cycle now")
    parser.add_argument("--status", action="store_true",
                        help="Show current system status")
    parser.add_argument("--macro", action="store_true",
                        help="Check macro gates (F1 + F2)")
    parser.add_argument("--collect-onchain", action="store_true",
                        help="Fetch Binance Futures OI/funding + optionally TVL/unlocks")
    parser.add_argument("--full", action="store_true",
                        help="Dipakai dengan --collect-onchain: fetch TVL + token unlocks juga")
    parser.add_argument("--backtest", action="store_true",
                        help="Jalankan backtest di data historis DuckDB")
    parser.add_argument("--from", dest="date_from", default=None,
                        help="Tanggal mulai backtest (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="Tanggal akhir backtest (YYYY-MM-DD)")
    parser.add_argument("--optimize-weights", action="store_true",
                        help="Jalankan Optuna optimizer untuk signal weights")
    parser.add_argument("--trials", type=int, default=300,
                        help="Jumlah Optuna trials (default: 300)")

    args = parser.parse_args()

    if args.fetch_history:
        logger.info("Phase 1: Fetching historical data...")
        fetch_all_historical()

    elif args.macro:
        result = fetch_all_macro()
        print(f"\nF1 Gate: {result['f1_gate']['reason']}")
        print(f"F2 Gate: {result['f2_gate']['label']}")
        print(f"F&G:     {result['macro']['fear_greed']['value']}")
        print(f"BTC.D:   {result['macro']['btc_dominance']:.1f}%")

    elif args.scan_once:
        run_scan_once()

    elif args.status:
        show_status()

    elif args.collect_onchain:
        logger.info("Phase 2: Collecting on-chain data...")
        collect_all_onchain(full=args.full)
        if args.full:
            logger.info("Collecting TVL data...")
            collect_all_tvl()
            logger.info("Collecting token unlock calendar...")
            collect_all_token_unlocks()

    elif args.backtest:
        from backtesting.harness import run_backtest
        run_backtest(date_from=args.date_from, date_to=args.date_to)

    elif args.optimize_weights:
        from backtesting.optimizer import run_optimization
        run_optimization(n_trials=args.trials)

    elif args.run:
        asyncio.run(live_loop())

    else:
        parser.print_help()
        print("\nQuick start:")
        print("  python3 main.py --fetch-history          ← Download 2 tahun data")
        print("  python3 main.py --collect-onchain --full ← Fetch on-chain + TVL + unlocks")
        print("  python3 main.py --scan-once              ← Test satu scan")
        print("  python3 main.py --backtest               ← Validasi strategi")
        print("  python3 main.py --optimize-weights       ← Cari bobot optimal")
        print("  python3 main.py --run                    ← Start live mode")


if __name__ == "__main__":
    main()
