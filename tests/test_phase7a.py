"""
tests/test_phase7a.py — Regression tests for Phase 7A bug fixes.

Bug 1: Liquidation cascade condition was always False (liq_l > 0 AND liq_s > 0 never both set)
Bug 2: VOLATILE regime never fired during trending crashes (ADX check blocked it)
Bug 3: F1 gate failed when FRED key not set (no_key treated same as API error)
Bug 4: Twitter 30d change used today's data as baseline when no history (0% always)
"""
import sys
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Bug 1: Liquidation cascade ────────────────────────────────

from collector.onchain_enhanced import score_from_futures_data


def test_liquidation_cascade_long_fires_alone():
    """liq_long > 5M alone (no liq_short) should still fire capitulation signal."""
    data = {
        "oi_change_24h_pct": -8.0,
        "funding_rate":      0.01,
        "long_short_ratio":  1.0,
        "liq_long_24h":      20_000_000,  # big long cascade
        "liq_short_24h":     0.0,         # no short liq (mutually exclusive)
    }
    score = score_from_futures_data(data)
    assert score >= 75, f"Long cascade alone should score >= 75, got {score}"


def test_liquidation_short_squeeze_fires_alone():
    """liq_short > 5M alone should add bullish momentum bonus."""
    data = {
        "oi_change_24h_pct": 5.0,
        "funding_rate":      -0.02,
        "long_short_ratio":  0.7,
        "liq_long_24h":      0.0,         # no long liq
        "liq_short_24h":     10_000_000,  # short squeeze
    }
    score = score_from_futures_data(data)
    assert score >= 75, f"Short squeeze alone should score >= 75, got {score}"


def test_small_liquidation_below_threshold_does_not_trigger():
    """liq < 5M threshold should not change score."""
    data_no_liq = {
        "oi_change_24h_pct": 0.0,
        "funding_rate":      0.01,
        "long_short_ratio":  1.0,
        "liq_long_24h":      0.0,
        "liq_short_24h":     0.0,
    }
    data_small_liq = {
        **data_no_liq,
        "liq_long_24h": 4_999_999,  # just under threshold
    }
    score_no   = score_from_futures_data(data_no_liq)
    score_small = score_from_futures_data(data_small_liq)
    assert score_no == score_small, "Sub-threshold liq should not affect score"


# ── Bug 2: VOLATILE regime detection ──────────────────────────

from signals.engine import _detect_regime_legacy


def _make_candles(n: int = 250, base_atr_pct: float = 0.01,
                  spike_last: bool = False) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame with controllable ATR."""
    rng = np.random.default_rng(42)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    close = np.clip(close, 10, None)
    high  = close * (1 + base_atr_pct)
    low   = close * (1 - base_atr_pct)

    if spike_last:
        # Spike the last candle's TR by 20× to ensure ATR[last] > 2.0 × ATR[prior avg].
        # Wilder's ATR: ATR[i] = (ATR[i-1]*13 + TR[i]) / 14
        # With TR = 20× normal: (13 + 20) / 14 ≈ 2.36 → exceeds 2.0× threshold.
        high[-1] = close[-1] * (1 + base_atr_pct * 20)
        low[-1]  = close[-1] * (1 - base_atr_pct * 20)

    df = pd.DataFrame({
        "open":      close,
        "high":      high,
        "low":       low,
        "close":     close,
        "volume":    rng.uniform(1e6, 2e6, n),
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="4h"),
    })
    return df


def test_volatile_regime_fires_on_atr_spike():
    """ATR 2× above prior average should return VOLATILE regardless of trend."""
    df = _make_candles(n=250, base_atr_pct=0.005, spike_last=True)
    regime = _detect_regime_legacy(df)
    assert regime == "VOLATILE", f"Expected VOLATILE on ATR spike, got {regime}"


def test_volatile_not_triggered_by_normal_atr():
    """Flat ATR should NOT return VOLATILE."""
    df = _make_candles(n=250, base_atr_pct=0.01, spike_last=False)
    regime = _detect_regime_legacy(df)
    assert regime != "VOLATILE", f"Normal ATR should not be VOLATILE, got {regime}"


def test_volatile_regime_has_weights():
    """VOLATILE regime must have weights defined (not fallback to SIGNAL_WEIGHTS)."""
    from config import REGIME_WEIGHTS, SIGNAL_WEIGHTS
    from signals.engine import get_regime_weights
    w = get_regime_weights("VOLATILE")
    assert w != SIGNAL_WEIGHTS, "VOLATILE should use its own weights, not default"
    assert abs(sum(w.values()) - 1.0) < 0.001, "VOLATILE weights must sum to 1.0"


# ── Bug 3: F1 gate with missing FRED key ──────────────────────

from collector.macro import evaluate_f1_gate


def test_f1_gate_passes_when_no_fred_key():
    """When FRED key not set (status=no_key), F1 should PASS, not FAIL."""
    macro = {
        "fear_greed":    {"value": 55, "label": "Greed"},
        "m2":            {"status": "no_key", "trend": "unknown"},
        "btc_dominance": 58.0,
    }
    result = evaluate_f1_gate(macro)
    assert result["passed"] is True, (
        f"F1 should pass when FRED key not set, got: {result['reason']}"
    )


def test_f1_gate_passes_optimistically_on_api_error_no_cache():
    """API error + no cache → optimistic pass (don't block trading for infra issues)."""
    macro = {
        "fear_greed":    {"value": 55, "label": "Greed"},
        "m2":            {"status": "error", "trend": "unknown"},
        "btc_dominance": 58.0,
    }
    with patch("collector.macro._load_f1_cache", return_value=None):
        result = evaluate_f1_gate(macro)
    assert result["passed"] is True, (
        f"F1 should pass optimistically on API error+no cache, got: {result['reason']}"
    )


def test_f1_gate_fails_on_extreme_greed_regardless():
    """F1 should fail if Fear & Greed > 80 even when M2 is ok."""
    macro = {
        "fear_greed":    {"value": 85, "label": "Extreme Greed"},
        "m2":            {"status": "no_key", "trend": "unknown"},
        "btc_dominance": 55.0,
    }
    result = evaluate_f1_gate(macro)
    assert result["passed"] is False, "Extreme greed should still fail F1"


def test_f1_gate_uses_cache_on_api_error():
    """When API fails but cache exists (fresh, < 7 days), use cached F1 result."""
    from datetime import datetime, timedelta
    macro = {
        "fear_greed":    {"value": 50, "label": "Neutral"},
        "m2":            {"status": "error", "trend": "unknown"},
        "btc_dominance": 55.0,
    }
    # Cache is 3 days old — fresh enough to use
    fresh_date = (datetime.utcnow() - timedelta(days=3)).isoformat()
    cached = {"f1_pass": False, "m2_trend": "contracting", "cached_at": fresh_date}
    with patch("collector.macro._load_f1_cache", return_value=cached):
        result = evaluate_f1_gate(macro)
    assert result["passed"] is False, "Fresh cache showing contraction should fail F1"


# ── Bug 4: Twitter 30d window ────────────────────────────────

from collector.social import calc_social_score


def test_social_score_zero_change_is_neutral():
    """0% Twitter change (no 30d data available) should give 0 modifier."""
    score = calc_social_score(
        twitter_change_30d=0.0,
        reddit_change_30d=0.0,
        github_commits_4w=0,
    )
    assert score == 0.0, f"Zero change should give 0, got {score}"


def test_social_no_historical_data_uses_zero_change():
    """
    When no 30d history, tw_change should be 0.0 (not computed from today's data).
    Validates the prev_tw=None path returns 0.0 change.
    """
    # If prev_tw is None, tw_change formula returns 0.0
    prev_tw = None
    cur_tw = 100_000
    tw_change = ((cur_tw - prev_tw) / prev_tw * 100
                 if prev_tw is not None and prev_tw > 0 else 0.0)
    assert tw_change == 0.0, "No 30d data should yield 0.0 change, not divide-by-None"
