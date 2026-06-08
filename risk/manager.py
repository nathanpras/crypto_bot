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
    """Format trade signal — compact, plain Indonesian."""
    from config import SECTOR_MAP, SIGNAL_THRESHOLD

    idr_rate = float(os.getenv("IDR_RATE", 17_800))
    symbol   = calc["symbol"]
    sym      = symbol.replace("USDT", "")
    is_paper = os.getenv("PAPER_TRADING", "true").lower() == "true"
    regime   = signal.get("regime", "")
    score    = signal.get("total_score", 0)
    signals  = signal.get("signals", {})

    entry    = calc["entry_price"]
    stop     = calc["stop_price"]
    tp1      = calc["tp1_price"]
    tp2      = calc["tp2_price"]
    max_buy  = entry * 1.02   # jangan beli kalau harga sudah naik > 2% dari entry
    pos_idr  = calc["position_usd"] * idr_rate
    risk_idr = calc["risk_usd"] * idr_rate

    pf = lambda p: f"${p:,.4f}" if p < 10 else (f"${p:,.3f}" if p < 100 else f"${p:,.2f}")

    # Title + label
    icon  = "🌪" if signal.get("strong") else "🔔"
    label = "PAPER" if is_paper else "LIVE"
    regime_short = {
        "TRENDING_BULL": "Tren naik 📈", "TRENDING_BEAR": "Tren turun 📉",
        "RANGING": "Sideways ↔️", "VOLATILE": "Volatil ⚡", "TRANSITIONING": "Transisi",
    }.get(regime, regime)

    # Verdikt singkat: kenapa masuk + apa risikonya (max 2 baris)
    positives, negatives = [], []
    if signals.get("macd_score", 0)      >= 70: positives.append("MACD kuat")
    if signals.get("trend_score", 0)     >= 65: positives.append("tren bagus")
    if signals.get("rsi_score", 0)       >= 65: positives.append("RSI oversold")
    if signals.get("onchain_score", 0)   >= 70: positives.append("smart money masuk")
    if signals.get("volume_score", 0)    >= 65: positives.append("volume besar")
    if signals.get("wyckoff_score", 0)   >= 65: positives.append("pola akumulasi")
    if signals.get("sentiment_score", 0) >= 65: positives.append("pasar oversold")

    if regime == "TRENDING_BEAR":          negatives.append("tren masih turun")
    if signals.get("trend_score", 50)  < 40: negatives.append("tren lemah")
    if signals.get("volume_score", 50) < 45: negatives.append("volume sepi")
    if signals.get("wyckoff_score", 50)< 40: negatives.append("belum ada akumulasi")
    if score < SIGNAL_THRESHOLD + 5:       negatives.append("skor pas di batas")
    if signal.get("unlock_penalty", 0) >= 5:
        negatives.append(f"token unlock -{signal['unlock_penalty']:.0f}pts")

    # Futures context (1 baris kalau ada)
    db      = get_db()
    fm_df   = db.get_futures_metrics(symbol)
    ctx     = ""
    if not fm_df.empty:
        fm   = fm_df.iloc[0]
        fund = float(fm.get("funding_rate") or 0)
        if fund < -0.01:   ctx = "Funding negatif 🟢 (shorts bayar longs, bagus)"
        elif fund > 0.03:  ctx = f"Funding tinggi 🔴 ({fund:+.4f}) — banyak longs, hati-hati"

    pos_usd    = calc["position_usd"]
    tp1_profit = pos_usd * 0.5 * (tp1 / entry - 1) * idr_rate   # 50% posisi di TP1
    tp2_profit = pos_usd * 0.5 * (tp2 / entry - 1) * idr_rate   # 50% sisanya di TP2
    total_profit = tp1_profit + tp2_profit

    lines = [
        f"{icon} <b>Signal {sym}</b>  <code>{score:.0f}/100</code>  [{label}]",
        f"{regime_short}",
        "",
        "📌 <b>Harga</b>",
        f"Beli di          : <b>{pf(entry)}</b>",
        f"Maksimal beli di : {pf(max_buy)}  <i>(lewat ini, skip)</i>",
        f"Cut loss         : {pf(stop)}  <i>(-{(1-stop/entry)*100:.1f}%)</i>",
        f"Target 1         : {pf(tp1)}  <i>(+{(tp1/entry-1)*100:.1f}%)</i>",
        f"Target 2         : {pf(tp2)}  <i>(+{(tp2/entry-1)*100:.1f}%)</i>",
        "",
        "💵 <b>Uang</b>",
        f"Modal      : <b>Rp {pos_idr:,.0f}</b>",
        f"Maks rugi  : <b>Rp {risk_idr:,.0f}</b>",
        f"Untung TP1 : <b>Rp {tp1_profit:,.0f}</b>  <i>(jual 50% posisi)</i>",
        f"Untung TP2 : <b>Rp {tp2_profit:,.0f}</b>  <i>(jual sisanya)</i>",
        f"Total jika hit semua : <b>Rp {total_profit:,.0f}</b>",
    ]

    # Kenapa masuk
    if positives:
        lines += ["", f"✅ {', '.join(positives[:3])}"]
    if negatives:
        lines += [f"⚠️ {', '.join(negatives[:3])}"]
    if ctx:
        lines += [f"ℹ️ {ctx}"]

    return "\n".join(lines)

