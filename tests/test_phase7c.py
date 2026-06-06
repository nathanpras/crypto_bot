"""
tests/test_phase7c.py — Phase 7C: VWAP, Volume Delta, BB Squeeze, Correlation filter.
"""
import sys
import pytest
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from signals.technical import (
    calc_vwap_score,
    calc_volume_delta_score,
    calc_bb_squeeze_score,
    calc_correlation_filter,
)


# ── Helpers ──────────────────────────────────────────────────

def _make_df(n=50, trend="flat", base=100.0, high_vol=False) -> pd.DataFrame:
    rng = np.random.default_rng(123)
    if trend == "up":
        close = base + np.linspace(0, 20, n) + rng.normal(0, 0.3, n)
    elif trend == "down":
        close = base - np.linspace(0, 20, n) + rng.normal(0, 0.3, n)
    else:
        close = base + rng.normal(0, 0.5, n)

    close = np.clip(close, 1.0, None)
    high  = close * 1.005
    low   = close * 0.995
    vol   = rng.uniform(1e6, 2e6, n)
    if high_vol:
        vol *= 3.0
    return pd.DataFrame({
        "open": close, "high": high, "low": low,
        "close": close, "volume": vol,
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="4h"),
    })


# ── VWAP score ────────────────────────────────────────────────

def test_vwap_score_price_above_rising_vwap_bullish():
    df = _make_df(50, trend="up")
    score = calc_vwap_score(df)
    assert score > 0, f"Uptrend should give positive VWAP modifier, got {score}"


def test_vwap_score_price_below_falling_vwap_bearish():
    df = _make_df(50, trend="down")
    score = calc_vwap_score(df)
    assert score < 0, f"Downtrend should give negative VWAP modifier, got {score}"


def test_vwap_score_flat_near_zero():
    df = _make_df(50, trend="flat")
    score = calc_vwap_score(df)
    assert -5 <= score <= 5, f"Flat should give near-zero VWAP modifier, got {score}"


def test_vwap_score_range():
    for trend in ["up", "down", "flat"]:
        score = calc_vwap_score(_make_df(50, trend=trend))
        assert -8 <= score <= 8, f"VWAP modifier out of range: {score}"


def test_vwap_score_empty_df_returns_zero():
    assert calc_vwap_score(pd.DataFrame()) == 0.0


# ── Volume delta score ────────────────────────────────────────

def _make_df_directional(n=30, buy_pct=0.7) -> pd.DataFrame:
    """Create candles where buy_pct fraction are up-candles (shuffled)."""
    rng   = np.random.default_rng(99)
    close = 100.0 + rng.normal(0, 0.2, n)
    opens = close.copy()
    # Randomly assign buy/sell so last 10 candles reflect the overall ratio
    is_buy = rng.random(n) < buy_pct
    for i in range(n):
        if is_buy[i]:
            opens[i] = close[i] - abs(rng.normal(0.5, 0.1))  # open below close
        else:
            opens[i] = close[i] + abs(rng.normal(0.5, 0.1))  # open above close
    vol = rng.uniform(1e6, 2e6, n)
    return pd.DataFrame({
        "open": opens, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": vol,
    })


def test_volume_delta_mostly_buy_is_positive():
    df = _make_df_directional(30, buy_pct=0.8)
    score = calc_volume_delta_score(df)
    assert score > 0, f"80% buy candles should give positive delta, got {score}"


def test_volume_delta_mostly_sell_is_negative():
    df = _make_df_directional(30, buy_pct=0.2)
    score = calc_volume_delta_score(df)
    assert score < 0, f"80% sell candles should give negative delta, got {score}"


def test_volume_delta_range():
    for pct in [0.1, 0.5, 0.9]:
        score = calc_volume_delta_score(_make_df_directional(30, buy_pct=pct))
        assert -10 <= score <= 10, f"Volume delta out of range: {score}"


def test_volume_delta_empty_returns_zero():
    assert calc_volume_delta_score(pd.DataFrame()) == 0.0


# ── BB squeeze score ──────────────────────────────────────────

def _make_squeeze_df(n=60, squeeze=True) -> pd.DataFrame:
    """
    squeeze=True: wide historical range, last 20 candles very tight (contrast triggers squeeze).
    squeeze=False: uniformly wide range (no squeeze).
    """
    rng = np.random.default_rng(77)
    if squeeze:
        # First 40 candles: wide range to establish avg
        wide  = 100.0 + rng.normal(0, 2.0, 40)
        # Last 20 candles: very tight — squeeze
        tight = wide[-1] + rng.normal(0, 0.05, 20)
        close = np.concatenate([wide, tight])
    else:
        close = 100.0 + rng.normal(0, 2.0, n)
    close = np.clip(close, 1.0, None)
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": rng.uniform(1e6, 2e6, n),
    })


def test_bb_squeeze_tight_bands_nonzero():
    df = _make_squeeze_df(60, squeeze=True)
    score = calc_bb_squeeze_score(df)
    assert score != 0.0, "Tight BB should produce non-zero modifier"


def test_bb_squeeze_range():
    for sq in [True, False]:
        score = calc_bb_squeeze_score(_make_squeeze_df(60, squeeze=sq))
        assert -8 <= score <= 10, f"BB squeeze out of range: {score}"


def test_bb_squeeze_empty_returns_zero():
    assert calc_bb_squeeze_score(pd.DataFrame()) == 0.0


def test_bb_squeeze_insufficient_data_returns_zero():
    # Only 10 candles — below the 25-candle minimum
    rng = np.random.default_rng(1)
    close = 100.0 + rng.normal(0, 0.5, 10)
    df = pd.DataFrame({"open": close, "high": close * 1.001,
                       "low": close * 0.999, "close": close,
                       "volume": rng.uniform(1e6, 2e6, 10)})
    assert calc_bb_squeeze_score(df) == 0.0


# ── Correlation filter ────────────────────────────────────────

def _corr_pair(n=30, corr_target=0.9) -> tuple:
    """Build two DataFrames with approx given correlation."""
    rng = np.random.default_rng(55)
    btc_ret = rng.normal(0, 0.02, n)
    if corr_target >= 0.8:
        coin_ret = btc_ret + rng.normal(0, 0.002, n)   # highly correlated
    else:
        coin_ret = rng.normal(0, 0.02, n)               # uncorrelated

    btc_close  = 50000 * np.cumprod(1 + btc_ret)
    coin_close = 100   * np.cumprod(1 + coin_ret)

    df_coin = pd.DataFrame({"close": coin_close})
    df_btc  = pd.DataFrame({"close": btc_close})
    return df_coin, df_btc


def test_corr_filter_no_penalty_in_bull_regime():
    df_c, df_b = _corr_pair(30, corr_target=0.1)  # low correlation
    score = calc_correlation_filter(df_c, df_b, "TRENDING_BULL")
    assert score == 0.0, "No penalty outside bear/volatile regimes"


def test_corr_filter_penalizes_decorrelated_in_bear():
    df_c, df_b = _corr_pair(30, corr_target=0.1)  # uncorrelated
    score = calc_correlation_filter(df_c, df_b, "TRENDING_BEAR")
    assert score < 0, f"Decorrelated in bear should penalize, got {score}"


def test_corr_filter_no_penalty_when_correlated_in_bear():
    df_c, df_b = _corr_pair(30, corr_target=0.9)  # highly correlated
    score = calc_correlation_filter(df_c, df_b, "TRENDING_BEAR")
    assert score == 0.0, f"Correlated in bear should have no penalty, got {score}"


def test_corr_filter_penalizes_volatile_regime_too():
    df_c, df_b = _corr_pair(30, corr_target=0.1)
    score = calc_correlation_filter(df_c, df_b, "VOLATILE")
    assert score < 0, "VOLATILE regime should also penalize decorrelated coins"


def test_corr_filter_empty_returns_zero():
    assert calc_correlation_filter(pd.DataFrame(), pd.DataFrame(), "TRENDING_BEAR") == 0.0
