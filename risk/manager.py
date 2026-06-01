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
    """Format a trade signal for Telegram notification."""
    idr_rate = float(os.getenv("IDR_RATE", 17_800))
    risk_idr = calc["risk_usd"] * idr_rate
    pos_idr  = calc["position_usd"] * idr_rate

    paper = "📄 PAPER TRADE" if os.getenv("PAPER_TRADING", "true").lower() == "true" else "💰 LIVE TRADE"
    strength = "🌪 PERFECT STORM" if signal.get("strong") else "🔔 SIGNAL"

    return f"""
{strength} — {calc['symbol']} {paper}
{'═'*35}
Score   : {signal['total_score']:.0f}/100
Regime  : {signal.get('regime','—')}
Tier    : {calc['tier']}

Entry   : ${calc['entry_price']:,.4f}
Stop    : ${calc['stop_price']:,.4f} (-{(1-calc['stop_price']/calc['entry_price'])*100:.1f}%)
TP1     : ${calc['tp1_price']:,.4f} (+{(calc['tp1_price']/calc['entry_price']-1)*100:.1f}%)
TP2     : ${calc['tp2_price']:,.4f} (+{(calc['tp2_price']/calc['entry_price']-1)*100:.1f}%)

R/R     : {calc['rr_ratio']:.1f}:1
Position: ${calc['position_usd']:.2f} (Rp {pos_idr:,.0f})
Risk    : ${calc['risk_usd']:.2f} (Rp {risk_idr:,.0f}) | {calc['risk_pct']:.2f}%

Signals:
  Trend:     {signal['signals'].get('trend_score',0):.0f}
  RSI:       {signal['signals'].get('rsi_score',0):.0f}
  MACD:      {signal['signals'].get('macd_score',0):.0f}
  Volume:    {signal['signals'].get('volume_score',0):.0f}
  Wyckoff:   {signal['signals'].get('wyckoff_score',0):.0f}
  On-chain:  {signal['signals'].get('onchain_score',0):.0f}
  Sentiment: {signal['signals'].get('sentiment_score',0):.0f}

[✅ CONFIRM] [❌ SKIP]
    """.strip()

