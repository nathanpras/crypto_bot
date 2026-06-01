# ============================================================
# signals/engine.py — Signal Scoring Engine (F5 Gate)
# ============================================================
# Runs on every 4H candle close for all coins.
# Calculates 7 weighted signals → total score 0-100.
# Fires Telegram alert when score ≥ 70.
# ============================================================

import pandas as pd
import pandas_ta as ta
import numpy as np
from loguru import logger

from config import COINS, SIGNAL_WEIGHTS, SIGNAL_THRESHOLD, SIGNAL_STRONG, FILTERS
from database import get_db
from collector.onchain_enhanced import calc_onchain_score_enhanced
from collector.narrative import get_sector_modifier
from collector.token_unlocks import get_unlock_penalty


def score_clamp(val: float) -> float:
    return max(0.0, min(100.0, float(val)))


# ── Signal 1: Trend Alignment (multi-timeframe) ──────────────

def calc_trend_score(df_4h: pd.DataFrame, df_1d: pd.DataFrame) -> float:
    """
    Score based on how many timeframes agree on direction.
    4H + 1D both bullish = 100. Mixed = 50. Both bearish = 0.
    """
    if df_4h.empty or len(df_4h) < 210:
        return 50.0

    # Calculate EMAs
    close_4h = df_4h["close"]
    ema20  = ta.ema(close_4h, 20).iloc[-1]
    ema50  = ta.ema(close_4h, 50).iloc[-1]
    ema200 = ta.ema(close_4h, 200).iloc[-1]
    price  = close_4h.iloc[-1]

    score = 50.0

    # EMA alignment (bullish: price > ema20 > ema50 > ema200)
    if price > ema20:    score += 12
    if ema20 > ema50:    score += 12
    if ema50 > ema200:   score += 13
    if price < ema20:    score -= 12
    if ema20 < ema50:    score -= 12
    if ema50 < ema200:   score -= 13

    # Daily timeframe confirmation
    if not df_1d.empty and len(df_1d) >= 50:
        close_1d = df_1d["close"]
        ema50_1d  = ta.ema(close_1d, 50).iloc[-1]
        if close_1d.iloc[-1] > ema50_1d:  score += 13
        else:                              score -= 13

    return score_clamp(score)


# ── Signal 2: RSI Momentum ────────────────────────────────────

def calc_rsi_score(df_4h: pd.DataFrame) -> float:
    """
    RSI 14 on 4H. Score based on:
    - Oversold (< 35) = high bullish score
    - Overbought (> 70) = low score (avoid chasing)
    - Divergence detection bonus
    """
    if df_4h.empty or len(df_4h) < 20:
        return 50.0

    close = df_4h["close"]
    rsi   = ta.rsi(close, 14)
    cur   = rsi.iloc[-1]
    prev  = rsi.iloc[-5:].mean()

    if pd.isna(cur):
        return 50.0

    # Base score from RSI level
    if cur < 30:      base = 85
    elif cur < 40:    base = 72
    elif cur < 50:    base = 60
    elif cur < 60:    base = 50
    elif cur < 70:    base = 38
    else:             base = 20  # Overbought — avoid

    # Momentum bonus: RSI rising from oversold
    momentum = cur - prev
    if momentum > 3 and cur < 50:   base = min(base + 10, 95)
    if momentum < -3 and cur > 50:  base = max(base - 10, 10)

    # Bullish divergence: price lower but RSI higher
    if len(close) >= 14:
        price_trend = close.iloc[-1] - close.iloc[-14]
        rsi_trend   = cur - rsi.iloc[-14]
        if price_trend < 0 and rsi_trend > 2:
            base = min(base + 12, 95)  # Bullish divergence bonus

    return score_clamp(base)


# ── Signal 3: MACD Momentum ───────────────────────────────────

def calc_macd_score(df_4h: pd.DataFrame) -> float:
    """MACD histogram direction and zero-line cross."""
    if df_4h.empty or len(df_4h) < 40:
        return 50.0

    close    = df_4h["close"]
    macd_df  = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is None or macd_df.empty:
        return 50.0

    col_hist  = [c for c in macd_df.columns if "h" in c.lower()]
    col_macd  = [c for c in macd_df.columns if "macd" in c.lower() and "h" not in c.lower() and "s" not in c.lower()]
    col_sig   = [c for c in macd_df.columns if "s" in c.lower() and "h" not in c.lower()]

    if not col_hist:
        return 50.0

    hist     = macd_df[col_hist[0]]
    cur_hist = hist.iloc[-1]
    prev_hist = hist.iloc[-2] if len(hist) > 1 else 0

    score = 50.0

    # Histogram direction
    if cur_hist > 0:                             score += 20
    if cur_hist > 0 and cur_hist > prev_hist:    score += 15  # Expanding bullish
    if cur_hist < 0:                             score -= 20
    if cur_hist < 0 and cur_hist < prev_hist:    score -= 15  # Expanding bearish

    # MACD line vs signal cross
    if col_macd and col_sig:
        macd_line = macd_df[col_macd[0]].iloc[-1]
        sig_line  = macd_df[col_sig[0]].iloc[-1]
        prev_macd = macd_df[col_macd[0]].iloc[-2] if len(macd_df) > 1 else macd_line
        prev_sig  = macd_df[col_sig[0]].iloc[-2] if len(macd_df) > 1 else sig_line

        # Bullish cross (MACD crosses above signal)
        if macd_line > sig_line and prev_macd <= prev_sig:
            score += 15

    return score_clamp(score)


# ── Signal 4: Volume Confirmation ────────────────────────────

def calc_volume_score(df_4h: pd.DataFrame) -> float:
    """
    Volume vs 20-period SMA.
    High volume on up candles, low volume on down = bullish.
    High volume on down candles = bearish.
    """
    if df_4h.empty or len(df_4h) < 25:
        return 50.0

    vol       = df_4h["volume"]
    close     = df_4h["close"]
    vol_sma   = vol.rolling(20).mean()

    cur_vol   = vol.iloc[-1]
    avg_vol   = vol_sma.iloc[-1]
    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0
    is_up     = close.iloc[-1] > close.iloc[-2]

    # Recent 5 candles: net buy pressure
    up_vol   = sum(vol.iloc[-5+i] for i in range(5) if close.iloc[-5+i] > close.iloc[-6+i])
    down_vol = sum(vol.iloc[-5+i] for i in range(5) if close.iloc[-5+i] <= close.iloc[-6+i])
    total_vol = up_vol + down_vol
    buy_pressure = up_vol / total_vol if total_vol > 0 else 0.5

    score = buy_pressure * 100  # 0–100 based on buy pressure

    # Volume ratio bonus
    if is_up and vol_ratio > 1.5:     score = min(score + 15, 95)
    if not is_up and vol_ratio > 1.5: score = max(score - 15, 5)
    if vol_ratio < 0.5:               score = 50  # Low volume = inconclusive

    return score_clamp(score)


# ── Signal 5: Wyckoff Phase Score ────────────────────────────

def calc_wyckoff_score(df_4h: pd.DataFrame) -> float:
    """
    Detect Wyckoff accumulation patterns.
    Looks for: Spring (fake breakdown on low volume) or LPS (higher low on low volume).
    """
    if df_4h.empty or len(df_4h) < 60:
        return 50.0

    close  = df_4h["close"]
    high   = df_4h["high"]
    low    = df_4h["low"]
    vol    = df_4h["volume"]
    vol_avg = vol.rolling(20).mean()

    score = 50.0

    # Find trading range (last 40 candles)
    range_low  = low.iloc[-40:].min()
    range_high = high.iloc[-40:].max()
    range_size = range_high - range_low

    if range_size <= 0:
        return 50.0

    cur_price  = close.iloc[-1]
    cur_vol    = vol.iloc[-1]
    cur_vol_avg = vol_avg.iloc[-1]

    # Position in range (0 = bottom, 1 = top)
    pos_in_range = (cur_price - range_low) / range_size

    # Spring detection: price near/below range low, LOW volume
    recent_low = low.iloc[-5:].min()
    if recent_low <= range_low * 1.01:  # Price touched or broke below range low
        vol_ratio = cur_vol / cur_vol_avg if cur_vol_avg > 0 else 1
        if vol_ratio < 0.7:  # Low volume — Spring!
            score = 85
            logger.debug(f"  SPRING DETECTED: vol_ratio={vol_ratio:.2f}")
        else:
            score = 45  # Breakdown with high volume = not spring

    # LPS detection: price making higher low after Spring
    elif pos_in_range < 0.3:  # Price near bottom of range
        # Check if previous candles went lower (creating higher low)
        recent_lows = low.iloc[-10:]
        if recent_lows.iloc[-1] > recent_lows.iloc[:-1].min():
            vol_trend = vol.iloc[-3:].mean() / vol.iloc[-10:-3].mean()
            if vol_trend < 0.8:  # Declining volume on pullback
                score = 75
                logger.debug("  LPS pattern: higher low on declining volume")

    # SOS detection: price breaking above range high on HIGH volume
    elif cur_price > range_high * 0.99:
        vol_ratio = cur_vol / cur_vol_avg if cur_vol_avg > 0 else 1
        if vol_ratio > 1.5:  # High volume breakout
            score = 80
            logger.debug(f"  SOS: breakout with vol_ratio={vol_ratio:.2f}")

    # Distribution warning: price at top of range with high volume
    if pos_in_range > 0.85 and cur_vol > cur_vol_avg * 1.3:
        score = max(score - 25, 10)

    return score_clamp(score)


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

def detect_regime(df_4h: pd.DataFrame) -> str:
    """Classify market regime using ADX."""
    if df_4h.empty or len(df_4h) < 30:
        return "UNKNOWN"

    adx_df = ta.adx(df_4h["high"], df_4h["low"], df_4h["close"], length=14)
    if adx_df is None or adx_df.empty:
        return "UNKNOWN"

    adx_col = [c for c in adx_df.columns if "ADX" in c and "+" not in c and "-" not in c]
    dmp_col = [c for c in adx_df.columns if "+" in c]
    dmn_col = [c for c in adx_df.columns if "-" in c]

    if not adx_col:
        return "UNKNOWN"

    adx = adx_df[adx_col[0]].iloc[-1]
    if pd.isna(adx):
        return "UNKNOWN"

    if adx > FILTERS["adx_trending"]:
        if dmp_col and dmn_col:
            dmp = adx_df[dmp_col[0]].iloc[-1]
            dmn = adx_df[dmn_col[0]].iloc[-1]
            return "TRENDING_BULL" if dmp > dmn else "TRENDING_BEAR"
        return "TRENDING"
    elif adx < FILTERS["adx_ranging"]:
        return "RANGING"
    else:
        return "TRANSITIONING"


# ── Main Score Engine ─────────────────────────────────────────

def score_coin(symbol: str, fear_greed: int = 50,
               funding_rate: float = 0,
               allowed_tiers: list = None) -> dict:
    """
    Calculate full signal score for one coin.
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

    # Weighted total — explicit mapping because SIGNAL_WEIGHTS keys differ from s dict keys
    total = (
        s["trend_score"]     * SIGNAL_WEIGHTS["trend_alignment"] +
        s["rsi_score"]       * SIGNAL_WEIGHTS["rsi_momentum"] +
        s["macd_score"]      * SIGNAL_WEIGHTS["macd_momentum"] +
        s["volume_score"]    * SIGNAL_WEIGHTS["volume_confirm"] +
        s["wyckoff_score"]   * SIGNAL_WEIGHTS["wyckoff_phase"] +
        s["onchain_score"]   * SIGNAL_WEIGHTS["onchain_signal"] +
        s["sentiment_score"] * SIGNAL_WEIGHTS["sentiment_score"]
    )

    # Phase 2 modifiers
    sector_mod  = get_sector_modifier(symbol, db)
    unlock_pen  = get_unlock_penalty(symbol, db)
    total       = score_clamp(total + sector_mod - unlock_pen)

    regime = detect_regime(df_4h)
    fired  = total >= SIGNAL_THRESHOLD   # Use final total
    strong = total >= SIGNAL_STRONG      # Use final total

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
    }

    # Store to DB
    db.upsert_signal(symbol, {**s, "total_score": total, "regime": regime})

    return result


def scan_all_coins(fear_greed: int = 50,
                   allowed_tiers: list = None) -> list[dict]:
    """
    Score all coins. Returns sorted list (highest score first).
    Called on every 4H candle close.
    """
    if allowed_tiers is None:
        allowed_tiers = [1, 2, 3]

    logger.info(f"Scanning {len(COINS)} coins | F&G: {fear_greed} | "
                f"Tiers: {allowed_tiers}")

    results = []
    for symbol in COINS:
        try:
            result = score_coin(symbol, fear_greed=fear_greed,
                                allowed_tiers=allowed_tiers)
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
