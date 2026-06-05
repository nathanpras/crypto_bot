# signals/technical.py — Phase 7C: Advanced Technical Indicators
"""
Four new signal components wired as score modifiers:
- VWAP score: is price above/below volume-weighted average price?
- Volume delta: directional buy pressure from up vs down candle volume
- BB squeeze: Bollinger Band contraction → imminent expansion signal
- Correlation filter: altcoin-BTC correlation gate in bear regimes
"""

import pandas as pd
import numpy as np
import ta as _ta
from loguru import logger


def calc_vwap_score(df: pd.DataFrame) -> float:
    """
    VWAP deviation score. Price above VWAP on rising volume = bullish.
    Uses rolling 20-period VWAP (session VWAP unavailable without tick data).
    Returns modifier: -8 to +8.
    """
    if df.empty or len(df) < 21:
        return 0.0

    tp  = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"]

    # Rolling 20-period VWAP
    tp_vol   = (tp * vol).rolling(20).sum()
    vol_sum  = vol.rolling(20).sum()
    vwap     = (tp_vol / vol_sum.replace(0, np.nan)).fillna(tp)

    price     = df["close"].iloc[-1]
    vwap_now  = vwap.iloc[-1]
    vwap_prev = vwap.iloc[-5]

    if pd.isna(vwap_now) or vwap_now == 0:
        return 0.0

    deviation_pct = (price - vwap_now) / vwap_now * 100
    vwap_rising   = vwap_now > vwap_prev

    if deviation_pct > 2.0 and vwap_rising:
        return 8.0     # Price well above rising VWAP → strong bullish
    elif deviation_pct > 0.5:
        return 4.0     # Price above VWAP
    elif deviation_pct < -2.0 and not vwap_rising:
        return -8.0    # Price well below falling VWAP → strong bearish
    elif deviation_pct < -0.5:
        return -4.0    # Price below VWAP
    return 0.0


def calc_volume_delta_score(df: pd.DataFrame) -> float:
    """
    Volume delta: directional buy/sell pressure over last 10 candles.
    Up candles (close > open) = buy volume; down = sell volume.
    Returns modifier: -10 to +10.
    """
    if df.empty or len(df) < 12:
        return 0.0

    recent = df.iloc[-10:]
    buy_vol  = recent.loc[recent["close"] >= recent["open"], "volume"].sum()
    sell_vol = recent.loc[recent["close"] <  recent["open"], "volume"].sum()
    total    = buy_vol + sell_vol

    if total == 0:
        return 0.0

    delta_ratio = (buy_vol - sell_vol) / total  # -1 to +1

    # Scale to -10 to +10
    modifier = round(delta_ratio * 10, 1)

    # Bonus: check if last 3 candles confirm direction
    last3 = df.iloc[-3:]
    last3_buy  = last3.loc[last3["close"] >= last3["open"], "volume"].sum()
    last3_sell = last3.loc[last3["close"] <  last3["open"], "volume"].sum()
    if last3_buy > last3_sell * 2:
        modifier = min(modifier + 2.0, 10.0)   # Recent acceleration
    elif last3_sell > last3_buy * 2:
        modifier = max(modifier - 2.0, -10.0)

    return float(max(-10.0, min(10.0, modifier)))


def calc_bb_squeeze_score(df: pd.DataFrame) -> float:
    """
    Bollinger Band squeeze: narrow bands = coiled spring = imminent move.
    Score is directional based on price position within the squeeze.
    Returns modifier: -8 to +10.
    """
    if df.empty or len(df) < 25:
        return 0.0

    close = df["close"]

    bb = _ta.volatility.BollingerBands(close, window=20, window_dev=2)
    upper = bb.bollinger_hband()
    lower = bb.bollinger_lband()
    mid   = bb.bollinger_mavg()

    if upper is None or lower is None or mid is None:
        return 0.0

    width_now  = (upper.iloc[-1] - lower.iloc[-1]) / mid.iloc[-1]
    width_avg  = ((upper - lower) / mid.replace(0, np.nan)).iloc[-20:].mean()

    if pd.isna(width_now) or pd.isna(width_avg) or width_avg == 0:
        return 0.0

    squeeze_ratio = width_now / width_avg  # < 1.0 = compressed

    price_in_band = (close.iloc[-1] - lower.iloc[-1]) / (
        upper.iloc[-1] - lower.iloc[-1]
    ) if (upper.iloc[-1] - lower.iloc[-1]) > 0 else 0.5

    if squeeze_ratio < 0.5:
        # Extreme squeeze — big move coming, direction from price position
        if price_in_band > 0.6:
            return 10.0   # Squeeze + price high in band = bullish breakout likely
        elif price_in_band < 0.4:
            return -8.0   # Squeeze + price low in band = bearish breakdown likely
        return 5.0         # Squeeze but indeterminate → slight bullish bias

    elif squeeze_ratio < 0.75:
        # Moderate squeeze
        if price_in_band > 0.55:
            return 5.0
        elif price_in_band < 0.45:
            return -4.0
        return 2.0

    # Band expanding (post-squeeze) — momentum confirmation
    if squeeze_ratio > 1.5 and price_in_band > 0.7:
        return 6.0    # Band expanding up
    if squeeze_ratio > 1.5 and price_in_band < 0.3:
        return -6.0   # Band expanding down

    return 0.0


# ── Migrated from engine.py (Phase 8B) ───────────────────────

def calc_trend_score(df_4h: pd.DataFrame, df_1d: pd.DataFrame) -> float:
    """
    Score based on how many timeframes agree on direction.
    4H + 1D both bullish = 100. Mixed = 50. Both bearish = 0.
    """
    if df_4h.empty or len(df_4h) < 210:
        return 50.0

    close_4h = df_4h["close"]
    ema20  = _ta.trend.EMAIndicator(close_4h, window=20).ema_indicator().iloc[-1]
    ema50  = _ta.trend.EMAIndicator(close_4h, window=50).ema_indicator().iloc[-1]
    ema200 = _ta.trend.EMAIndicator(close_4h, window=200).ema_indicator().iloc[-1]
    price  = close_4h.iloc[-1]

    score = 50.0

    if price > ema20:    score += 12
    if ema20 > ema50:    score += 12
    if ema50 > ema200:   score += 13
    if price < ema20:    score -= 12
    if ema20 < ema50:    score -= 12
    if ema50 < ema200:   score -= 13

    if not df_1d.empty and len(df_1d) >= 50:
        close_1d = df_1d["close"]
        ema50_1d  = _ta.trend.EMAIndicator(close_1d, window=50).ema_indicator().iloc[-1]
        if close_1d.iloc[-1] > ema50_1d:  score += 13
        else:                              score -= 13

    return float(max(0.0, min(100.0, score)))


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
    rsi   = _ta.momentum.RSIIndicator(close, window=14).rsi()
    cur   = rsi.iloc[-1]
    prev  = rsi.iloc[-5:].mean()

    if pd.isna(cur):
        return 50.0

    if cur < 30:      base = 85
    elif cur < 40:    base = 72
    elif cur < 50:    base = 60
    elif cur < 60:    base = 50
    elif cur < 70:    base = 38
    else:             base = 20

    momentum = cur - prev
    if momentum > 3 and cur < 50:   base = min(base + 10, 95)
    if momentum < -3 and cur > 50:  base = max(base - 10, 10)

    if len(close) >= 14:
        price_trend = close.iloc[-1] - close.iloc[-14]
        rsi_trend   = cur - rsi.iloc[-14]
        if price_trend < 0 and rsi_trend > 2:
            base = min(base + 12, 95)

    return float(max(0.0, min(100.0, base)))


def calc_macd_score(df_4h: pd.DataFrame) -> float:
    """MACD histogram direction and zero-line cross."""
    if df_4h.empty or len(df_4h) < 40:
        return 50.0

    close = df_4h["close"]
    macd_ind = _ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    hist      = macd_ind.macd_diff()
    macd_line = macd_ind.macd()
    sig_line  = macd_ind.macd_signal()

    if hist is None or hist.dropna().empty:
        return 50.0

    cur_hist  = hist.iloc[-1]
    prev_hist = hist.iloc[-2] if len(hist) > 1 else 0

    score = 50.0

    if cur_hist > 0:                             score += 20
    if cur_hist > 0 and cur_hist > prev_hist:    score += 15
    if cur_hist < 0:                             score -= 20
    if cur_hist < 0 and cur_hist < prev_hist:    score -= 15

    if macd_line is not None and sig_line is not None:
        cur_macd  = macd_line.iloc[-1]
        cur_sig   = sig_line.iloc[-1]
        prev_macd = macd_line.iloc[-2] if len(macd_line) > 1 else cur_macd
        prev_sig  = sig_line.iloc[-2]  if len(sig_line)  > 1 else cur_sig

        if cur_macd > cur_sig and prev_macd <= prev_sig:
            score += 15

    return float(max(0.0, min(100.0, score)))


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

    up_vol   = sum(vol.iloc[-5+i] for i in range(5) if close.iloc[-5+i] > close.iloc[-6+i])
    down_vol = sum(vol.iloc[-5+i] for i in range(5) if close.iloc[-5+i] <= close.iloc[-6+i])
    total_vol = up_vol + down_vol
    buy_pressure = up_vol / total_vol if total_vol > 0 else 0.5

    score = buy_pressure * 100

    if is_up and vol_ratio > 1.5:     score = min(score + 15, 95)
    if not is_up and vol_ratio > 1.5: score = max(score - 15, 5)
    if vol_ratio < 0.5:               score = 50

    return float(max(0.0, min(100.0, score)))


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

    range_low  = low.iloc[-40:].min()
    range_high = high.iloc[-40:].max()
    range_size = range_high - range_low

    if range_size <= 0:
        return 50.0

    cur_price  = close.iloc[-1]
    cur_vol    = vol.iloc[-1]
    cur_vol_avg = vol_avg.iloc[-1]

    pos_in_range = (cur_price - range_low) / range_size

    recent_low = low.iloc[-5:].min()
    if recent_low <= range_low * 1.01:
        vol_ratio = cur_vol / cur_vol_avg if cur_vol_avg > 0 else 1
        if vol_ratio < 0.7:
            score = 85
            logger.debug(f"  SPRING DETECTED: vol_ratio={vol_ratio:.2f}")
        else:
            score = 45

    elif pos_in_range < 0.3:
        recent_lows = low.iloc[-10:]
        if recent_lows.iloc[-1] > recent_lows.iloc[:-1].min():
            vol_trend = vol.iloc[-3:].mean() / vol.iloc[-10:-3].mean()
            if vol_trend < 0.8:
                score = 75
                logger.debug("  LPS pattern: higher low on declining volume")

    elif cur_price > range_high * 0.99:
        vol_ratio = cur_vol / cur_vol_avg if cur_vol_avg > 0 else 1
        if vol_ratio > 1.5:
            score = 80
            logger.debug(f"  SOS: breakout with vol_ratio={vol_ratio:.2f}")

    if pos_in_range > 0.85 and cur_vol > cur_vol_avg * 1.3:
        score = max(score - 25, 10)

    return float(max(0.0, min(100.0, score)))


# ── Normalized wrappers (0-100) for VWAP, VolumeDelta, BB Squeeze ────────────

def calc_vwap_normalized(df: pd.DataFrame) -> float:
    """T6: VWAP deviation as 0-100. Maps modifier -8..+8 → 0..100."""
    mod = calc_vwap_score(df)
    return float(max(0.0, min(100.0, (mod + 8) / 16 * 100)))


def calc_volume_delta_normalized(df: pd.DataFrame) -> float:
    """T7: Volume delta as 0-100. Maps modifier -10..+10 → 0..100."""
    mod = calc_volume_delta_score(df)
    return float(max(0.0, min(100.0, (mod + 10) / 20 * 100)))


def calc_bb_squeeze_normalized(df: pd.DataFrame) -> float:
    """T8: BB squeeze as 0-100. Maps modifier -8..+10 → 0..100."""
    mod = calc_bb_squeeze_score(df)
    return float(max(0.0, min(100.0, (mod + 8) / 18 * 100)))


def calc_correlation_filter(df_coin: pd.DataFrame,
                             df_btc: pd.DataFrame,
                             regime: str) -> float:
    """
    BTC correlation filter. In bearish regimes, altcoins that
    are UNCORRELATED with BTC are suspicious (idiosyncratic pump).
    Returns modifier: -5 to 0.
    Only penalizes, never rewards (correlation itself is not bullish).
    """
    if regime not in ("TRENDING_BEAR", "VOLATILE"):
        return 0.0   # Only apply in bear/volatile regimes

    if df_coin.empty or df_btc.empty:
        return 0.0

    # Align on common length (last 30 candles)
    n = min(30, len(df_coin), len(df_btc))
    if n < 10:
        return 0.0

    r_coin = df_coin["close"].iloc[-n:].pct_change().dropna()
    r_btc  = df_btc["close"].iloc[-n:].pct_change().dropna()

    # Align lengths after pct_change
    min_len = min(len(r_coin), len(r_btc))
    if min_len < 5:
        return 0.0

    r_coin = r_coin.iloc[-min_len:].values
    r_btc  = r_btc.iloc[-min_len:].values

    try:
        corr = float(np.corrcoef(r_coin, r_btc)[0, 1])
    except Exception:
        return 0.0

    if pd.isna(corr):
        return 0.0

    # In bear/volatile regime: low correlation with BTC = suspicious pump
    if corr < 0.3:
        return -5.0   # Decorrelated in bear = likely unsustainable
    elif corr < 0.5:
        return -2.0   # Weakly correlated = caution
    return 0.0        # Correlated enough — no penalty
