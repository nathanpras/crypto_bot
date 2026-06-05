"""Tests for Phase 8 Task 11: Per-regime Optuna optimizer."""
import pytest
from signals.registry import get_signal_ids
from backtesting.optimizer import optimize_weights_for_regime, optimize_all_regimes


def make_synthetic_data(n=200, seed=42):
    """Returns (historical_signals, labels) — synthetic but realistic."""
    import random
    random.seed(seed)
    signal_ids = get_signal_ids()
    signals = [{sid: random.uniform(20, 80) for sid in signal_ids} for _ in range(n)]
    labels = [random.gauss(0.002, 0.015) for _ in range(n)]
    return signals, labels


def test_optimize_weights_returns_all_signals():
    signals, labels = make_synthetic_data(50)
    weights = optimize_weights_for_regime("bull", signals, labels, n_trials=5)
    assert set(weights.keys()) == set(get_signal_ids())


def test_optimize_weights_sum_to_one():
    signals, labels = make_synthetic_data(50)
    weights = optimize_weights_for_regime("bear", signals, labels, n_trials=5)
    assert abs(sum(weights.values()) - 1.0) < 0.01


def test_optimize_weights_all_positive():
    signals, labels = make_synthetic_data(50)
    weights = optimize_weights_for_regime("sideways", signals, labels, n_trials=5)
    for sid, w in weights.items():
        assert w > 0, f"Weight for {sid} should be positive"


def test_optimize_all_regimes_returns_5_regimes():
    signals, labels = make_synthetic_data(50)
    data_by_regime = {regime: (signals, labels) for regime in ["bull", "bear", "sideways", "volatile", "recovery"]}
    all_weights = optimize_all_regimes(data_by_regime, n_trials=3)
    assert set(all_weights.keys()) == {"bull", "bear", "sideways", "volatile", "recovery"}


def test_optimize_all_regimes_each_sums_to_one():
    signals, labels = make_synthetic_data(50)
    data_by_regime = {r: (signals, labels) for r in ["bull", "bear", "sideways", "volatile", "recovery"]}
    all_weights = optimize_all_regimes(data_by_regime, n_trials=3)
    for regime, weights in all_weights.items():
        assert abs(sum(weights.values()) - 1.0) < 0.01, f"{regime} sum={sum(weights.values())}"


from backtesting.walk_forward import rolling_walk_forward_splits, is_consistent


def test_rolling_walk_forward_returns_splits():
    # 2 years daily data = 720 points, enough for 6+ windows
    data = [{"idx": i, "value": i * 1.0} for i in range(720)]
    splits = rolling_walk_forward_splits(data, train_months=9, val_months=3)
    assert len(splits) >= 6, f"Expected >=6 splits, got {len(splits)}"


def test_rolling_walk_forward_split_structure():
    data = [{"idx": i} for i in range(720)]
    splits = rolling_walk_forward_splits(data, train_months=9, val_months=3)
    for split in splits:
        assert "train" in split
        assert "val" in split
        assert "window_idx" in split
        assert len(split["train"]) > 0
        assert len(split["val"]) > 0


def test_rolling_walk_forward_insufficient_data_returns_empty():
    data = [{"idx": i} for i in range(100)]  # Too little data
    splits = rolling_walk_forward_splits(data, train_months=9, val_months=3)
    assert splits == []


def test_is_consistent_passes_low_std():
    scores = [0.62, 0.65, 0.60, 0.63, 0.61, 0.64]
    assert is_consistent(scores, std_threshold=0.15) is True


def test_is_consistent_fails_high_std():
    scores = [0.90, 0.10, 0.85, 0.15, 0.80, 0.20]
    assert is_consistent(scores, std_threshold=0.15) is False


def test_is_consistent_requires_min_6_windows():
    scores = [0.62, 0.63, 0.61, 0.60, 0.64]  # Only 5
    assert is_consistent(scores, std_threshold=0.15) is False
