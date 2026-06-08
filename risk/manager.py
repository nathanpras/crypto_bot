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
    """Format trade signal untuk Telegram dalam bahasa Indonesia yang mudah dibaca."""
    from config import SECTOR_MAP, SIGNAL_THRESHOLD

    idr_rate = float(os.getenv("IDR_RATE", 17_800))
    risk_idr = calc["risk_usd"] * idr_rate
    pos_idr  = calc["position_usd"] * idr_rate
    symbol   = calc["symbol"]
    sym      = symbol.replace("USDT", "")

    is_paper  = os.getenv("PAPER_TRADING", "true").lower() == "true"
    is_strong = signal.get("strong", False)
    regime    = signal.get("regime", "")
    score     = signal.get("total_score", 0)
    signals   = signal.get("signals", {})

    # Title
    if is_strong:
        title = f"🌪 <b>PERFECT STORM — {sym}!</b>"
    else:
        title = f"🔔 <b>Signal Masuk — {sym}</b>"
    if is_paper:
        title += "\n<i>(Paper trade — simulasi, bukan uang sungguhan)</i>"

    # Plain regime description
    regime_plain = {
        "TRENDING_BULL":  "Tren lagi naik",
        "TRENDING_BEAR":  "Tren lagi turun ⚠️",
        "RANGING":        "Harga sideways",
        "VOLATILE":       "Harga lagi liar/volatil",
        "TRANSITIONING":  "Masa transisi",
    }.get(regime, regime)

    # Entry/exit plan
    entry = calc["entry_price"]
    stop  = calc["stop_price"]
    tp1   = calc["tp1_price"]
    tp2   = calc["tp2_price"]
    stop_pct = (1 - stop / entry) * 100
    tp1_pct  = (tp1 / entry - 1) * 100
    tp2_pct  = (tp2 / entry - 1) * 100

    pf = lambda p: f"${p:,.4f}" if p < 10 else (f"${p:,.3f}" if p < 100 else f"${p:,.2f}")

    # Signal strengths and weaknesses in plain language
    sig_good, sig_bad = [], []
    checks = [
        ("trend_score",     "Tren naik kuat",          "Tren masih turun/lemah",    65, 45),
        ("rsi_score",       "RSI oversold (potensi bounce)", "Momentum lemah",       65, 45),
        ("volume_score",    "Volume besar mengkonfirmasi",   "Volume masih sepi",    65, 45),
        ("wyckoff_score",   "Pola akumulasi terbentuk", "Belum ada pola akumulasi",  65, 45),
        ("onchain_score",   "Pemain besar masuk (onchain)",  "Onchain lemah",        65, 45),
        ("macd_score",      "Momentum MACD kuat",       "MACD flat/bearish",         70, 40),
        ("sentiment_score", "Pasar oversold/takut",     "Sentiment negatif",         65, 45),
    ]
    for key, good_label, bad_label, good_thresh, bad_thresh in checks:
        v = signals.get(key, 0)
        if v >= good_thresh:
            sig_good.append(f"✅ {good_label} ({v:.0f})")
        elif v < bad_thresh:
            sig_bad.append(f"⚠️ {bad_label} ({v:.0f})")

    # Warnings
    warnings = []
    if regime == "TRENDING_BEAR":
        warnings.append("⚠️ Tren DOT sedang turun — signal ini melawan tren, risiko lebih tinggi")
    if signals.get("trend_score", 50) < 40:
        warnings.append("⚠️ Indikator tren sangat lemah (24/100) — pertimbangkan skip")
    if score < SIGNAL_THRESHOLD + 5:
        warnings.append(f"⚠️ Skor pas di batas ({score:.0f}/100) — signal marginal")
    unlock_pen = signal.get("unlock_penalty", 0)
    if unlock_pen >= 5:
        warnings.append(f"⚠️ Ada token unlock besar (−{unlock_pen:.0f} poin)")

    # Futures context
    db    = get_db()
    fm_df = db.get_futures_metrics(symbol)
    ctx_lines = []
    if not fm_df.empty:
        fm     = fm_df.iloc[0]
        oi_chg = float(fm.get("oi_change_24h_pct") or 0)
        fund   = float(fm.get("funding_rate") or 0)
        if abs(oi_chg) >= 1:
            ctx_lines.append(
                f"Open interest naik {oi_chg:+.1f}% (banyak posisi baru terbuka)" if oi_chg > 0
                else f"Open interest turun {oi_chg:.1f}% (banyak posisi ditutup)"
            )
        if fund < -0.01:
            ctx_lines.append("Funding negatif 🟢 — shorts bayar longs (bagus untuk long)")
        elif fund > 0.03:
            ctx_lines.append(f"Funding tinggi 🔴 ({fund:+.4f}) — banyak longs, hati-hati")

    chain   = SECTOR_MAP.get(symbol, "")
    tvl_row = db.get_sector_tvl(chain) if chain else {}
    tvl_30d = float(tvl_row.get("tvl_change_30d") or 0)
    if tvl_30d > 5:
        ctx_lines.append(f"TVL ekosistem naik {tvl_30d:+.1f}% (30 hari) — fundamental bagus 🟢")
    elif tvl_30d < -5:
        ctx_lines.append(f"TVL ekosistem turun {tvl_30d:.1f}% (30 hari) 🔴")

    # Build message
    lines = [
        title,
        f"Kondisi: <b>{regime_plain}</b>  |  Skor: <b>{score:.0f}/100</b>",
        "",
        "──────────────────────────",
        "💰 <b>Rencana Posisi</b>",
        f"Beli di   : <b>{pf(entry)}</b>",
        f"Cut loss  : {pf(stop)} (kalau turun {stop_pct:.1f}%, langsung keluar)",
        f"Target 1  : {pf(tp1)} (ambil 50% profit di +{tp1_pct:.1f}%)",
        f"Target 2  : {pf(tp2)} (sisanya trailing stop di +{tp2_pct:.1f}%)",
        "",
        f"Modal     : <b>Rp {pos_idr:,.0f}</b> (${calc['position_usd']:.2f})",
        f"Maks rugi : Rp {risk_idr:,.0f} (${calc['risk_usd']:.2f}) — {calc['risk_pct']:.1f}% portfolio",
    ]

    if warnings:
        lines += ["", "──────────────────────────"]
        lines += warnings

    if sig_good or sig_bad:
        lines += ["", "──────────────────────────", "📊 <b>Kenapa signal ini masuk?</b>"]
        lines += sig_good[:3]
        lines += sig_bad[:2]

    if ctx_lines:
        lines += ["", "──────────────────────────", "📋 <b>Info Tambahan</b>"]
        lines += [f"• {c}" for c in ctx_lines]

    return "\n".join(lines)

