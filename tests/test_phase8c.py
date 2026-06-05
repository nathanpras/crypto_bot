"""Tests for Phase 8 Task 10: Engine regime-aware weights."""
import pytest
from database import Database
from config import DEFAULT_WEIGHTS_PHASE8
from signals.registry import get_signal_ids
from signals.engine import get_regime_weights_from_db, calc_composite_score_phase8, detect_regime


def test_default_weights_all_regimes_present():
    required = {"bull", "bear", "sideways", "volatile", "recovery"}
    assert set(DEFAULT_WEIGHTS_PHASE8.keys()) == required


def test_default_weights_all_signals_per_regime():
    signal_ids = set(get_signal_ids())
    for regime, weights in DEFAULT_WEIGHTS_PHASE8.items():
        assert set(weights.keys()) == signal_ids, f"{regime} missing signals: {signal_ids - set(weights.keys())}"


def test_default_weights_sum_to_one():
    for regime, weights in DEFAULT_WEIGHTS_PHASE8.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01, f"{regime} weights sum to {total}, expected ~1.0"


def test_get_regime_weights_from_db_fallback_to_default():
    db = Database(":memory:")
    weights = get_regime_weights_from_db("bull", db)
    db.close()
    expected = DEFAULT_WEIGHTS_PHASE8["bull"]
    for sid in get_signal_ids():
        assert sid in weights
    assert abs(sum(weights.values()) - 1.0) < 0.01


def test_get_regime_weights_from_db_uses_saved_weights():
    db = Database(":memory:")
    custom = {sid: 1/32 for sid in get_signal_ids()}
    db.save_optimized_weights("bull", custom)
    weights = get_regime_weights_from_db("bull", db)
    db.close()
    for sid in get_signal_ids():
        assert sid in weights
    assert abs(sum(weights.values()) - 1.0) < 0.01


def test_get_regime_weights_normalizes_if_not_summing_to_one():
    db = Database(":memory:")
    # Save weights that don't sum to 1
    unscaled = {sid: 0.1 for sid in get_signal_ids()}  # sums to 3.2
    db.save_optimized_weights("bear", unscaled)
    weights = get_regime_weights_from_db("bear", db)
    db.close()
    assert abs(sum(weights.values()) - 1.0) < 0.01


def test_calc_composite_score_all_neutral():
    signal_ids = get_signal_ids()
    scores = {sid: 50.0 for sid in signal_ids}
    weights = {sid: 1/32 for sid in signal_ids}
    result = calc_composite_score_phase8(scores, weights)
    assert abs(result - 50.0) < 0.1


def test_calc_composite_score_all_high():
    signal_ids = get_signal_ids()
    scores = {sid: 90.0 for sid in signal_ids}
    weights = {sid: 1/32 for sid in signal_ids}
    result = calc_composite_score_phase8(scores, weights)
    assert result > 85.0


def test_calc_composite_score_range():
    signal_ids = get_signal_ids()
    import random
    random.seed(42)
    scores = {sid: random.uniform(0, 100) for sid in signal_ids}
    weights = {sid: 1/32 for sid in signal_ids}
    result = calc_composite_score_phase8(scores, weights)
    assert 0.0 <= result <= 100.0


def test_detect_regime_returns_valid():
    valid = {"bull", "bear", "sideways", "volatile", "recovery"}
    # Test with None/empty (fallback)
    result = detect_regime(None)
    assert result in valid


def test_detect_regime_with_candles():
    import numpy as np
    import pandas as pd
    np.random.seed(1)
    n = 220
    price = 30000.0
    rows = []
    for i in range(n):
        price *= (1 + np.random.normal(0.002, 0.012))
        rows.append({
            "open": price*0.999, "high": price*1.007, "low": price*0.993,
            "close": price, "volume": abs(np.random.normal(800_000_000, 100_000_000)),
        })
    df = pd.DataFrame(rows)
    result = detect_regime(df)
    assert result in {"bull", "bear", "sideways", "volatile", "recovery"}
