# ============================================================
# signals/engine.py — Signal Scoring Engine (F5 Gate)
# ============================================================
# Runs on every 4H candle close for all coins.
# Calculates 7 weighted signals → total score 0-100.
# Fires Telegram alert when score ≥ 70.
# ============================================================

import pandas as pd
import ta as _ta
import numpy as np
from loguru import logger

from datetime import datetime
from config import COINS, SIGNAL_WEIGHTS, SIGNAL_THRESHOLD, SIGNAL_STRONG, FILTERS, KILL_ZONES_UTC
from config import REGIME_WEIGHTS, KILL_ZONE_BONUS
from database import get_db
from collector.onchain_enhanced import calc_onchain_score_enhanced
from collector.narrative import get_sector_modifier
from collector.token_unlocks import get_unlock_penalty
from collector.news import get_news_gate
from collector.options import get_options_modifier
from collector.social import get_social_modifier
from collector.whale import get_whale_modifier
from signals.technical import (
    calc_vwap_score, calc_volume_delta_score,
    calc_bb_squeeze_score, calc_correlation_filter,
    calc_trend_score, calc_rsi_score, calc_macd_score,
    calc_volume_score, calc_wyckoff_score,
)


def score_clamp(val: float) -> float:
    return max(0.0, min(100.0, float(val)))


def get_regime_weights(regime: str) -> dict:
    """Return adaptive signal weights for the given market regime."""
    return REGIME_WEIGHTS.get(regime, SIGNAL_WEIGHTS)


def get_kill_zone_modifier() -> tuple[bool, float]:
    """
    Return (in_kill_zone, modifier).
    Kill zones: London open 07-10 UTC, NY open 13-16 UTC.
    """
    now     = datetime.utcnow()
    now_min = now.hour * 60 + now.minute
    for (sh, sm, eh, em) in KILL_ZONES_UTC:
        start = sh * 60 + sm
        end   = eh * 60 + em
        if start <= now_min < end:
            return True, float(KILL_ZONE_BONUS)
    return False, 0.0


# ── Signals 1-5 migrated to signals/technical.py (Phase 8B) ──
# calc_trend_score, calc_rsi_score, calc_macd_score,
# calc_volume_score, calc_wyckoff_score are imported above.

# ── Signal 6: On-Chain Score ──────────────────────────────────

def calc_onchain_score(symbol: str, db) -> float:
    """
    On-chain score: BTC/ETH pakai CoinMetrics, altcoin pakai Binance Futures.
    Delegate ke calc_onchain_score_enhanced dari collector/onchain_enhanced.py.
    """
    return calc_onchain_score_enhanced(symbol, db)


# ── Signal 7: Sentiment Score ─────────────────────────────────

def calc_sentiment_score(fear_greed: int, funding_rate: float = 0) -> float:
    """
    Combined sentiment from Fear & Greed + Funding Rate.
    Contrarian: extreme fear = good time to buy.
    Extreme greed = risky, avoid.
    """
    # Fear & Greed score (contrarian)
    if fear_greed < 20:    fg_score = 85  # Extreme fear = buy opportunity
    elif fear_greed < 35:  fg_score = 70
    elif fear_greed < 50:  fg_score = 55
    elif fear_greed < 65:  fg_score = 45
    elif fear_greed < 80:  fg_score = 30
    else:                   fg_score = 15  # Extreme greed = dangerous

    # Funding rate (negative = shorts paying longs = bullish setup)
    if funding_rate < -0.01:   fr_score = 80
    elif funding_rate < 0:     fr_score = 65
    elif funding_rate < 0.01:  fr_score = 50
    elif funding_rate < 0.05:  fr_score = 35
    else:                       fr_score = 15  # Very high = overleveraged longs

    return score_clamp(fg_score * 0.6 + fr_score * 0.4)


# ── Regime Detection ──────────────────────────────────────────

def _detect_regime_legacy(df_4h: pd.DataFrame) -> str:
    """Classify market regime using ADX and ATR volatility (Phase 6 style).
    Returns: TRENDING_BULL | TRENDING_BEAR | RANGING | VOLATILE | TRANSITIONING
    Used internally by score_coin for legacy REGIME_WEIGHTS lookup.
    """
    if df_4h.empty or len(df_4h) < 50:
        return "TRANSITIONING"

    close = df_4h["close"]
    high  = df_4h["high"]
    low   = df_4h["low"]

    # ATR for volatility detection — check VOLATILE before directional regimes
    # Use prior 20 bars (exclude current) to avoid self-referential comparison
    atr_series = _ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
    if not atr_series.empty and len(atr_series.dropna()) > 21:
        atr_now = atr_series.iloc[-1]
        atr_avg = atr_series.iloc[-21:-1].mean()  # prior 20, not including current
        if atr_avg > 0 and atr_now > atr_avg * 2.0:
            # High ATR spike = VOLATILE regardless of trend direction
            return "VOLATILE"

    adx_ind = _ta.trend.ADXIndicator(df_4h["high"], df_4h["low"], df_4h["close"], window=14)
    adx = adx_ind.adx().iloc[-1]

    if pd.isna(adx):
        return "TRANSITIONING"

    if adx > FILTERS["adx_trending"]:
        dmp = adx_ind.adx_pos().iloc[-1]
        dmn = adx_ind.adx_neg().iloc[-1]
        return "TRENDING_BULL" if dmp > dmn else "TRENDING_BEAR"
    elif adx < FILTERS["adx_ranging"]:
        return "RANGING"
    else:
        return "TRANSITIONING"


def detect_regime(df_4h) -> str:
    """
    Detect market regime from 4h candles DataFrame (Phase 8 style).
    Returns: 'bull' | 'bear' | 'sideways' | 'volatile' | 'recovery'
    """
    if df_4h is None or len(df_4h) < 50:
        return "sideways"

    try:
        import pandas_ta as _pta
        closes = df_4h["close"]
        ema50 = _pta.ema(closes, length=50)
        ema200 = _pta.ema(closes, length=200)

        if ema50 is None or ema200 is None:
            return "sideways"

        last_close = float(closes.iloc[-1])
        e50 = float(ema50.iloc[-1])
        e200 = float(ema200.iloc[-1])

        # Volatility: ATR / price ratio
        atr = _pta.atr(df_4h["high"], df_4h["low"], closes, length=14)
        atr_pct = float(atr.iloc[-1]) / last_close if atr is not None else 0

        # 30-bar price change
        price_chg = (last_close - float(closes.iloc[-30])) / float(closes.iloc[-30]) if len(closes) >= 30 else 0

        if atr_pct > 0.04:
            return "volatile"
        elif last_close > e50 > e200 and price_chg > 0.05:
            return "bull"
        elif last_close < e50 < e200 and price_chg < -0.05:
            return "bear"
        elif last_close > e50 and price_chg > 0.02:
            return "recovery"
        else:
            return "sideways"
    except Exception:
        return "sideways"


# ── Main Score Engine ─────────────────────────────────────────

def score_coin(symbol: str, fear_greed: int = 50,
               funding_rate: float = 0,
               allowed_tiers: list = None,
               extended_ctx: dict = None) -> dict:
    """
    Calculate full signal score for one coin.
    extended_ctx: optional Phase 7D data (stablecoin_flows, bybit_basis, defillama_fees)
    Returns dict with all sub-scores and total.
    """
    if allowed_tiers is None:
        allowed_tiers = [1, 2, 3]

    tier = COINS.get(symbol, {}).get("tier", 3)
    if tier not in allowed_tiers:
        return {
            "symbol": symbol, "total_score": 0,
            "fired": False, "blocked": f"Tier {tier} not allowed (BTC.D gate)"
        }

    db    = get_db()
    df_4h = db.get_candles(symbol, "4h", limit=220)
    df_1d = db.get_candles(symbol, "1d", limit=60)

    if df_4h.empty or len(df_4h) < 50:
        return {"symbol": symbol, "total_score": 0, "fired": False,
                "error": "Insufficient data"}

    # Calculate all signals
    s = {}
    s["trend_score"]     = calc_trend_score(df_4h, df_1d)
    s["rsi_score"]       = calc_rsi_score(df_4h)
    s["macd_score"]      = calc_macd_score(df_4h)
    s["volume_score"]    = calc_volume_score(df_4h)
    s["wyckoff_score"]   = calc_wyckoff_score(df_4h)
    s["onchain_score"]   = calc_onchain_score(symbol, db)
    s["sentiment_score"] = calc_sentiment_score(fear_greed, funding_rate)

    # Phase 6: detect regime first → use adaptive weights (legacy ADX-based)
    regime  = _detect_regime_legacy(df_4h)
    weights = get_regime_weights(regime)

    total = (
        s["trend_score"]     * weights["trend_alignment"] +
        s["rsi_score"]       * weights["rsi_momentum"] +
        s["macd_score"]      * weights["macd_momentum"] +
        s["volume_score"]    * weights["volume_confirm"] +
        s["wyckoff_score"]   * weights["wyckoff_phase"] +
        s["onchain_score"]   * weights["onchain_signal"] +
        s["sentiment_score"] * weights["sentiment_score"]
    )

    # Phase 2 modifiers
    sector_mod  = get_sector_modifier(symbol, db)
    unlock_pen  = get_unlock_penalty(symbol, db)
    total       = score_clamp(total + sector_mod - unlock_pen)

    # Phase 4: News hard gate
    news_check = get_news_gate(symbol, db)
    if news_check["blocked"]:
        return {
            "symbol":           symbol,
            "tier":             tier,
            "regime":           "BLOCKED",
            "total_score":      0.0,
            "fired":            False,
            "strong":           False,
            "signals":          {},
            "price":            df_4h["close"].iloc[-1],
            "timestamp":        df_4h["timestamp"].iloc[-1],
            "sector_modifier":  sector_mod,
            "unlock_penalty":   unlock_pen,
            "news_modifier":    0.0,
            "options_modifier": 0.0,
            "kill_zone_active":   False,
            "kill_zone_modifier": 0.0,
            "blocked_reason":   f"NEWS: {news_check['reason']}",
        }

    # Phase 4: Score modifiers
    news_mod    = news_check["modifier"]
    options_mod = get_options_modifier(symbol, db)
    total       = score_clamp(total + news_mod + options_mod)

    # Phase 5: Social + Whale modifiers
    social_mod = get_social_modifier(symbol, db)
    whale_mod  = get_whale_modifier(symbol, db)
    total      = score_clamp(total + social_mod + whale_mod)

    # Phase 6: Kill zone bonus
    in_kill_zone, kz_mod = get_kill_zone_modifier()
    total  = score_clamp(total + kz_mod)

    # Phase 7C: Advanced technical modifiers
    vwap_mod   = calc_vwap_score(df_4h)
    vdelta_mod = calc_volume_delta_score(df_4h)
    bb_mod     = calc_bb_squeeze_score(df_4h)

    # BTC data for correlation filter (only needed for non-BTC coins)
    if symbol != "BTCUSDT":
        df_btc = db.get_candles("BTCUSDT", "4h", limit=35)
        corr_mod = calc_correlation_filter(df_4h, df_btc, regime)
    else:
        corr_mod = 0.0

    total = score_clamp(total + vwap_mod + vdelta_mod + bb_mod + corr_mod)

    # Phase 7D: Extended macro modifiers (pre-fetched, shared across coins)
    stable_mod = 0.0
    basis_mod  = 0.0
    fees_mod   = 0.0
    if extended_ctx:
        stable_flows = extended_ctx.get("stablecoin_flows", {})
        stable_mod   = float(stable_flows.get("modifier", 0.0))

        basis_data = extended_ctx.get("bybit_basis", {})
        if symbol in basis_data:
            from collector.macro_extended import get_basis_modifier
            basis_mod = get_basis_modifier(symbol, basis_data)

        fees_data = extended_ctx.get("defillama_fees", {})
        if fees_data:
            from config import SECTOR_MAP
            from collector.macro_extended import get_fees_ecosystem_score
            chain = SECTOR_MAP.get(symbol, "")
            fees_mod = get_fees_ecosystem_score(chain, fees_data)

    total = score_clamp(total + stable_mod + basis_mod + fees_mod)

    fired  = total >= SIGNAL_THRESHOLD
    strong = total >= SIGNAL_STRONG

    result = {
        "symbol":           symbol,
        "tier":             tier,
        "regime":           regime,
        "total_score":      round(total, 1),
        "fired":            fired,
        "strong":           strong,
        "signals":          {k: round(v, 1) for k, v in s.items()},
        "price":            df_4h["close"].iloc[-1],
        "timestamp":        df_4h["timestamp"].iloc[-1],
        "sector_modifier":  sector_mod,
        "unlock_penalty":   unlock_pen,
        "news_modifier":    news_mod,
        "options_modifier": options_mod,
        "social_modifier":  social_mod,
        "whale_modifier":   whale_mod,
        "kill_zone_active":   in_kill_zone,
        "kill_zone_modifier": kz_mod,
        "vwap_modifier":      vwap_mod,
        "vdelta_modifier":    vdelta_mod,
        "bb_modifier":        bb_mod,
        "corr_modifier":      corr_mod,
        "stable_modifier":    stable_mod,
        "basis_modifier":     basis_mod,
        "fees_modifier":      fees_mod,
    }

    # Store to DB
    db.upsert_signal(symbol, {**s, "total_score": total, "regime": regime})

    return result


def get_regime_weights_from_db(regime: str, db) -> dict:
    """
    Load optimized weights for the given regime from DB.
    Falls back to DEFAULT_WEIGHTS_PHASE8 if DB has no weights for this regime.
    Validates all 32 signal IDs present. Normalizes if sum != 1.0.
    """
    from config import DEFAULT_WEIGHTS_PHASE8
    from signals.registry import get_signal_ids

    weights = db.get_optimized_weights(regime) if db is not None else {}

    if not weights:
        weights = DEFAULT_WEIGHTS_PHASE8.get(regime, DEFAULT_WEIGHTS_PHASE8["bull"]).copy()

    signal_ids = get_signal_ids()
    # Ensure all 32 signals present, fill missing with small default
    for sid in signal_ids:
        if sid not in weights:
            weights[sid] = 0.01

    # Normalize to sum=1.0
    total = sum(weights.values())
    if total > 0 and abs(total - 1.0) > 0.001:
        weights = {k: v / total for k, v in weights.items()}

    return weights


def calc_composite_score_phase8(scores: dict, weights: dict) -> float:
    """
    Compute weighted sum of all 32 signal scores.
    scores: dict{signal_id: float 0-100}
    weights: dict{signal_id: float 0-1, sums to 1.0}
    Returns float 0-100.
    """
    total = 0.0
    w_sum = 0.0
    for sid, w in weights.items():
        s = scores.get(sid, 50.0)
        total += s * w
        w_sum += w
    if w_sum == 0:
        return 50.0
    return float(max(0.0, min(100.0, total / w_sum * 1.0)))


def scan_all_coins(fear_greed: int = 50,
                   allowed_tiers: list = None,
                   extended_ctx: dict = None) -> list[dict]:
    """
    Score all coins. Returns sorted list (highest score first).
    Called on every 4H candle close.
    extended_ctx: Phase 7D data fetched once before the scan loop.
    """
    if allowed_tiers is None:
        allowed_tiers = [1, 2, 3]

    logger.info(f"Scanning {len(COINS)} coins | F&G: {fear_greed} | "
                f"Tiers: {allowed_tiers}")

    results = []
    for symbol in COINS:
        try:
            result = score_coin(symbol, fear_greed=fear_greed,
                                allowed_tiers=allowed_tiers,
                                extended_ctx=extended_ctx)
            results.append(result)
        except Exception as e:
            logger.error(f"Error scoring {symbol}: {e}")

    results.sort(key=lambda x: x.get("total_score", 0), reverse=True)

    # Log summary
    fired  = [r for r in results if r.get("fired")]
    strong = [r for r in results if r.get("strong")]

    logger.info(f"Scan complete: {len(fired)} signals fired "
                f"({len(strong)} strong)")

    for r in fired:
        strength = "🌪 PERFECT STORM" if r.get("strong") else "🔔 SIGNAL"
        logger.info(f"  {strength}: {r['symbol']} | "
                    f"Score: {r['total_score']} | "
                    f"Regime: {r['regime']} | "
                    f"Price: {r['price']:.4f}")

    return results
