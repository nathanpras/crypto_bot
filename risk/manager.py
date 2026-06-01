# ============================================================
# risk/manager.py — Risk Management & Position Sizing
# ============================================================

import os
import pandas as pd
from loguru import logger
from dotenv import load_dotenv

from config import (COINS, RISK_PER_TRADE, PERFECT_STORM_MULTIPLIER,
                    MAX_OPEN_POSITIONS, MAX_DAILY_LOSS_PCT,
                    STOP_LOSS_PCT, MIN_RR_RATIO, PORTFOLIO_ALLOCATION,
                    SIGNAL_STRONG)
from database import get_db

load_dotenv()


def get_portfolio_usd() -> float:
    """Get current tradeable portfolio value in USD."""
    idr_total = float(os.getenv("PORTFOLIO_IDR", 10_000_000))
    idr_rate  = float(os.getenv("IDR_RATE", 17_800))
    return idr_total / idr_rate


def calc_position_size(symbol: str, entry_price: float,
                       signal_score: float,
                       portfolio_usd: float = None) -> dict:
    """
    Calculate position size using half-Kelly with tier caps.

    Args:
        symbol:       e.g. "BTCUSDT"
        entry_price:  planned entry price
        signal_score: 0-100 signal confluence score
        portfolio_usd: total portfolio in USD

    Returns:
        dict with: position_usd, quantity, risk_usd, stop_price,
                   tp1_price, tp2_price, rr_ratio, size_multiplier
    """
    if portfolio_usd is None:
        portfolio_usd = get_portfolio_usd()

    tier     = COINS.get(symbol, {}).get("tier", 3)
    base_risk = RISK_PER_TRADE.get(tier, 0.01)

    # Perfect Storm multiplier
    size_mult = PERFECT_STORM_MULTIPLIER if signal_score >= SIGNAL_STRONG else 1.0

    # Only use the TRADING portion of portfolio for position sizing
    trading_pct = PORTFOLIO_ALLOCATION["category_kings"] + PORTFOLIO_ALLOCATION["moonshots"]
    trading_portfolio = portfolio_usd * trading_pct

    risk_pct = base_risk * size_mult
    risk_usd = trading_portfolio * risk_pct
    stop_pct = STOP_LOSS_PCT.get(tier, 0.10)

    # Position size from risk
    position_usd = risk_usd / stop_pct
    quantity     = position_usd / entry_price

    # Stop and targets
    stop_price = entry_price * (1 - stop_pct)
    tp1_price  = entry_price * (1 + stop_pct * 2.5)   # 2.5R
    tp2_price  = entry_price * (1 + stop_pct * 6.0)   # 6R

    # Validate R/R
    risk   = entry_price - stop_price
    reward = tp1_price - entry_price
    rr_ratio = reward / risk if risk > 0 else 0

    return {
        "symbol":         symbol,
        "tier":           tier,
        "entry_price":    round(entry_price, 8),
        "stop_price":     round(stop_price, 8),
        "tp1_price":      round(tp1_price, 8),
        "tp2_price":      round(tp2_price, 8),
        "quantity":       round(quantity, 6),
        "position_usd":   round(position_usd, 2),
        "risk_usd":       round(risk_usd, 2),
        "risk_pct":       round(risk_pct * 100, 2),
        "rr_ratio":       round(rr_ratio, 2),
        "size_multiplier": size_mult,
        "portfolio_usd":  round(portfolio_usd, 2),
        "valid":          rr_ratio >= MIN_RR_RATIO,
        "reject_reason":  None if rr_ratio >= MIN_RR_RATIO
                          else f"R/R {rr_ratio:.1f} < minimum {MIN_RR_RATIO}",
    }


def check_portfolio_guards(new_symbol: str) -> dict:
    """
    Run all portfolio-level safety checks before allowing a new trade.
    Returns: {"allowed": bool, "reason": str}
    """
    db = get_db()

    # 1. Max open positions
    open_trades = db.get_open_trades()
    if len(open_trades) >= MAX_OPEN_POSITIONS:
        return {
            "allowed": False,
            "reason": f"Max {MAX_OPEN_POSITIONS} positions already open "
                      f"({len(open_trades)} current)"
        }

    # 2. No duplicate positions in same coin
    if new_symbol in open_trades.get("symbol", pd.Series()).values:
        return {"allowed": False, "reason": f"Already have open position in {new_symbol}"}

    # 3. Daily loss limit
    try:
        today_pnl = db.conn.execute("""
            SELECT COALESCE(SUM(pnl_usd), 0) as pnl
            FROM trades
            WHERE DATE(closed_at) = CURRENT_DATE AND status = 'closed'
        """).fetchone()[0]

        portfolio_usd = get_portfolio_usd()
        daily_loss_pct = abs(today_pnl) / portfolio_usd if today_pnl < 0 else 0

        if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
            return {
                "allowed": False,
                "reason": f"Daily loss limit hit: -{daily_loss_pct*100:.1f}% "
                          f"(max {MAX_DAILY_LOSS_PCT*100:.0f}%)"
            }
    except Exception:
        pass  # DB might not have data yet

    # 4. Correlation guard: don't stack too many correlated alts
    tier3_open = 0
    try:
        for _, row in open_trades.iterrows():
            if COINS.get(row["symbol"], {}).get("tier") == 3:
                tier3_open += 1
        if tier3_open >= 2:
            coin_tier = COINS.get(new_symbol, {}).get("tier", 3)
            if coin_tier == 3:
                return {"allowed": False,
                        "reason": "Max 2 Tier 3 positions simultaneously"}
    except Exception:
        pass

    return {"allowed": True, "reason": "All checks passed ✓"}


def format_trade_for_telegram(calc: dict, signal: dict) -> str:
    """Format trade signal untuk Telegram. Termasuk Phase 2 context section."""
    from config import SECTOR_MAP, SIGNAL_WEIGHTS

    idr_rate = float(os.getenv("IDR_RATE", 17_800))
    risk_idr = calc["risk_usd"] * idr_rate
    pos_idr  = calc["position_usd"] * idr_rate

    paper    = "📄 PAPER TRADE" if os.getenv("PAPER_TRADING", "true").lower() == "true" else "💰 LIVE TRADE"
    strength = "🌪 PERFECT STORM" if signal.get("strong") else "🔔 SIGNAL"

    # Signal breakdown dengan progress bar
    signals  = signal.get("signals", {})

    def bar(score):
        filled = int(score / 10)
        return "█" * filled + "░" * (10 - filled)

    weight_map = [
        ("trend_score",     "Trend    ", SIGNAL_WEIGHTS["trend_alignment"]),
        ("rsi_score",       "RSI      ", SIGNAL_WEIGHTS["rsi_momentum"]),
        ("volume_score",    "Volume   ", SIGNAL_WEIGHTS["volume_confirm"]),
        ("wyckoff_score",   "Wyckoff  ", SIGNAL_WEIGHTS["wyckoff_phase"]),
        ("onchain_score",   "On-Chain ", SIGNAL_WEIGHTS["onchain_signal"]),
        ("macd_score",      "MACD     ", SIGNAL_WEIGHTS["macd_momentum"]),
        ("sentiment_score", "Sentiment", SIGNAL_WEIGHTS["sentiment_score"]),
    ]

    signal_lines = []
    for key, label, weight in weight_map:
        val = signals.get(key, 0)
        signal_lines.append(
            f"  {label} {bar(val)} {val:.0f}  ({weight*100:.0f}%)"
        )

    # Phase 2 context
    sector_mod  = signal.get("sector_modifier", 0)
    unlock_pen  = signal.get("unlock_penalty", 0)
    score_adj   = (f"+{sector_mod}" if sector_mod >= 0 else str(sector_mod))
    unlock_adj  = (f"-{unlock_pen}" if unlock_pen > 0 else "✓ none")

    # Futures context (ambil dari DB jika ada)
    symbol  = calc["symbol"]
    db      = get_db()
    fm_df   = db.get_futures_metrics(symbol)
    if not fm_df.empty:
        fm     = fm_df.iloc[0]
        oi_chg = float(fm.get("oi_change_24h_pct") or 0)
        fund   = float(fm.get("funding_rate") or 0)
        oi_str = f"OI 24h  : {oi_chg:+.1f}%"
        fr_str = f"Funding : {fund:+.4f} {'🟢' if fund < 0 else '🔴'}"
    else:
        oi_str = "OI 24h  : N/A"
        fr_str = "Funding : N/A"

    # TVL context
    chain   = SECTOR_MAP.get(symbol, "")
    tvl_row = db.get_sector_tvl(chain) if chain else {}
    tvl_30d = float(tvl_row.get("tvl_change_30d") or 0)
    tvl_icon = "🟢" if tvl_30d > 5 else "🔴" if tvl_30d < -5 else "⚪"
    tvl_str  = f"Sector  : {chain} TVL {tvl_30d:+.1f}% (30d) {tvl_icon}"

    return f"""
{strength} — {symbol} {paper}
{'═'*42}
Score   : {signal['total_score']:.0f}/100  ({score_adj} sector, -{unlock_pen} unlock)
Regime  : {signal.get('regime','—')}
Tier    : {calc['tier']}

Entry   : ${calc['entry_price']:,.4f}
Stop    : ${calc['stop_price']:,.4f} (-{(1-calc['stop_price']/calc['entry_price'])*100:.1f}%)
TP1     : ${calc['tp1_price']:,.4f} (+{(calc['tp1_price']/calc['entry_price']-1)*100:.1f}%)
TP2     : ${calc['tp2_price']:,.4f} (+{(calc['tp2_price']/calc['entry_price']-1)*100:.1f}%)

R/R     : {calc['rr_ratio']:.1f}:1
Position: ${calc['position_usd']:.2f} (Rp {pos_idr:,.0f})
Risk    : ${calc['risk_usd']:.2f} (Rp {risk_idr:,.0f}) | {calc['risk_pct']:.2f}%

── Signal Breakdown ──────────────────────
{chr(10).join(signal_lines)}

── Context ───────────────────────────────
{tvl_str}
Unlock  : {unlock_adj}
{oi_str}
{fr_str}
    """.strip()

