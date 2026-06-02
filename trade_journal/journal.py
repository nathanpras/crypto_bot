# trade_journal/journal.py
import os
from datetime import datetime, timedelta
import pandas as pd
from config import COINS, RISK_PER_TRADE


def calc_tp_prices(entry_price: float = None, stop_price: float = None,
                   entry: float = None, stop: float = None):
    """Return (tp1, tp2) — 2.5R dan 5.0R dari entry/stop."""
    ep = entry_price if entry_price is not None else entry
    sp = stop_price if stop_price is not None else stop
    risk_pct = (ep - sp) / ep
    tp1 = round(ep * (1 + risk_pct * 2.5), 4)
    tp2 = round(ep * (1 + risk_pct * 5.0), 4)
    return tp1, tp2


def calc_pnl(symbol: str, entry_price: float, exit_price: float,
             stop_price: float, portfolio_usd: float) -> dict:
    """Hitung P&L dari trade yang ditutup."""
    pnl_pct    = (exit_price - entry_price) / entry_price
    risk_pct   = (entry_price - stop_price) / entry_price
    r_multiple = round(pnl_pct / risk_pct, 2) if risk_pct > 0 else 0.0
    tier       = COINS.get(symbol, {}).get("tier", 3)
    risk_usd   = portfolio_usd * RISK_PER_TRADE.get(tier, 0.01)
    pnl_usd    = round(risk_usd * r_multiple, 2)
    idr_rate   = float(os.getenv("IDR_RATE", "17800"))
    pnl_idr    = round(pnl_usd * idr_rate, 0)
    return {
        "pnl_pct":    round(pnl_pct, 4),
        "pnl_usd":    pnl_usd,
        "pnl_idr":    pnl_idr,
        "r_multiple": r_multiple,
    }


def format_open_msg(symbol: str, entry: float, stop: float,
                    tp1: float, tp2: float,
                    signal_score, signal_linked: bool) -> str:
    risk_pct = (entry - stop) / entry * 100
    msg = (
        f"✅ <b>Trade dibuka</b>\n"
        f"{symbol} | Entry ${entry:.4f} | Stop ${stop:.4f}\n"
        f"TP1: ${tp1:.4f} (+{(tp1/entry-1)*100:.1f}%) | "
        f"TP2: ${tp2:.4f} (+{(tp2/entry-1)*100:.1f}%)\n"
        f"Risk: {risk_pct:.1f}%"
    )
    if signal_linked and signal_score is not None:
        msg += f"\n🔗 Linked ke sinyal score {signal_score:.0f}"
    else:
        msg += "\n⚠️ Tidak ada sinyal APEX dalam 48 jam — dicatat tanpa link"
    return msg


def format_close_msg(symbol: str, exit_price: float, exit_reason: str,
                     pnl_usd: float, pnl_idr: float,
                     r_multiple: float, hold_hours: float) -> str:
    icon     = "✅" if pnl_usd >= 0 else "❌"
    pnl_sign = "+" if pnl_usd >= 0 else ""
    return (
        f"{icon} <b>Trade ditutup</b>\n"
        f"{symbol} | Exit ${exit_price:.4f} ({exit_reason})\n"
        f"P&L: {pnl_sign}${pnl_usd:.2f} (Rp {pnl_idr:+,.0f})\n"
        f"R-Multiple: {r_multiple:+.2f}R | Hold: {hold_hours:.0f} jam"
    )


def generate_weekly_report(trades_df: pd.DataFrame,
                            date_from: str, date_to: str) -> str:
    if trades_df.empty:
        return (
            f"📊 <b>APEX Weekly Report</b>\n"
            f"{date_from} – {date_to}\n\n"
            f"Tidak ada trade minggu ini."
        )

    total    = len(trades_df)
    wins     = int((trades_df["pnl_usd"] > 0).sum())
    losses   = total - wins
    win_rate = wins / total
    pnl_sum  = trades_df["pnl_usd"].sum()
    idr_sum  = trades_df["pnl_idr"].sum() if "pnl_idr" in trades_df else pnl_sum * 17800
    avg_r    = trades_df["r_multiple"].mean()

    best_idx  = trades_df["pnl_usd"].idxmax()
    worst_idx = trades_df["pnl_usd"].idxmin()
    best      = trades_df.loc[best_idx]
    worst     = trades_df.loc[worst_idx]

    by_coin = trades_df.groupby("symbol").agg(
        count=("pnl_usd", "count"),
        wins=("pnl_usd", lambda x: int((x > 0).sum())),
        pnl=("pnl_usd", "sum"),
    )
    coin_lines = "\n".join(
        f"{sym:<10} {int(row['count'])} trade | "
        f"{int(row['wins'])}W {int(row['count']-row['wins'])}L | "
        f"${row['pnl']:+.2f}"
        for sym, row in by_coin.iterrows()
    )

    sig_section = ""
    if "signal_score" in trades_df.columns:
        high   = trades_df[trades_df["signal_score"] >= 80]
        medium = trades_df[
            (trades_df["signal_score"] >= 70) &
            (trades_df["signal_score"] <  80)
        ]
        high_wr   = (high["pnl_usd"] > 0).mean() * 100   if len(high)   > 0 else 0
        medium_wr = (medium["pnl_usd"] > 0).mean() * 100 if len(medium) > 0 else 0
        sig_section = (
            f"\n── Signal Accuracy ───────────────────\n"
            f"Score ≥80   → {len(high)} trade | {high_wr:.0f}% WR "
            f"{'✅' if high_wr >= 60 else '⚠️'}\n"
            f"Score 70-79 → {len(medium)} trade | {medium_wr:.0f}% WR "
            f"{'✅' if medium_wr >= 60 else '⚠️'}"
        )

    sign = "+" if pnl_sum >= 0 else ""
    best_score  = f"{best.get('signal_score', 0):.0f}" if pd.notna(best.get("signal_score")) else "—"
    worst_score = f"{worst.get('signal_score', 0):.0f}" if pd.notna(worst.get("signal_score")) else "—"

    return (
        f"📊 <b>APEX Weekly Report</b> — {date_from} – {date_to}\n"
        f"{'═'*39}\n"
        f"Trades  : {total}  |  Win: {wins}  |  Loss: {losses}\n"
        f"Win rate: {win_rate*100:.0f}%  |  Avg R: {avg_r:.1f}R\n"
        f"P&L     : {sign}${pnl_sum:.2f}  (Rp {idr_sum:+,.0f})\n"
        f"\n── Per Coin ──────────────────────────\n"
        f"{coin_lines}\n"
        f"\n── Best Trade ────────────────────────\n"
        f"{best['symbol']} ${best['pnl_usd']:+.2f} | Score {best_score} | {best['r_multiple']:.1f}R\n"
        f"\n── Worst Trade ───────────────────────\n"
        f"{worst['symbol']} ${worst['pnl_usd']:+.2f} | Score {worst_score} | {worst['r_multiple']:.1f}R"
        f"{sig_section}\n"
        f"{'═'*39}"
    )


GAP_THRESHOLD_WINRATE = 0.20
GAP_THRESHOLD_AVG_R   = 0.50
MIN_SAMPLE_TRADES     = 5


def detect_performance_gap(live_trades: pd.DataFrame, db):
    """Bandingkan live performance vs ekspektasi backtest terakhir."""
    if len(live_trades) < MIN_SAMPLE_TRADES:
        return None

    last_bt = db.get_last_deployed_backtest()
    if not last_bt:
        return None

    live_wr    = float((live_trades["pnl_usd"] > 0).mean())
    live_avg_r = float(live_trades["r_multiple"].mean())
    bt_wr      = last_bt["val_win_rate"] / 100.0
    bt_avg_r   = last_bt.get("avg_r", 2.0) or 2.0

    wr_gap = bt_wr - live_wr
    r_gap  = bt_avg_r - live_avg_r

    if wr_gap >= GAP_THRESHOLD_WINRATE or r_gap >= GAP_THRESHOLD_AVG_R:
        return {
            "live_wr":    live_wr,
            "live_avg_r": live_avg_r,
            "bt_wr":      bt_wr,
            "bt_avg_r":   bt_avg_r,
            "wr_gap":     wr_gap,
            "r_gap":      r_gap,
        }
    return None


def format_gap_alert(gap: dict) -> str:
    return (
        f"⚠️ <b>APEX Performance Gap Detected</b>\n\n"
        f"Live: {gap['live_wr']*100:.0f}% WR | {gap['live_avg_r']:.1f}R avg\n"
        f"Backtest ekspektasi: {gap['bt_wr']*100:.0f}% WR | {gap['bt_avg_r']:.1f}R avg\n"
        f"Gap: -{gap['wr_gap']*100:.0f}pp win rate | -{gap['r_gap']:.1f}R\n\n"
        f"Saran: <code>python main.py --optimize-weights</code>"
    )
