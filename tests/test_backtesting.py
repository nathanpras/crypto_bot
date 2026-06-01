# tests/test_backtesting.py
import pytest
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtesting.harness import (
    build_signal_series,
    simulate_trades,
    calc_metrics,
)


@pytest.fixture
def sample_price_df():
    """DataFrame OHLCV 500 candle 4H untuk backtest."""
    np.random.seed(123)
    n = 500
    price = 100.0
    prices = []
    for _ in range(n):
        price *= (1 + np.random.normal(0.0005, 0.022))
        prices.append(max(price, 1.0))

    return pd.DataFrame({
        "timestamp": pd.date_range("2023-01-01", periods=n, freq="4h"),
        "open":   [p * 0.999 for p in prices],
        "high":   [p * 1.012 for p in prices],
        "low":    [p * 0.988 for p in prices],
        "close":  prices,
        "volume": [abs(np.random.normal(1_000_000, 200_000)) for _ in range(n)],
    })


@pytest.fixture
def sample_signal_scores(sample_price_df):
    """Skor sinyal 0-100 untuk setiap candle."""
    np.random.seed(456)
    n = len(sample_price_df)
    scores = np.where(
        np.random.random(n) < 0.15,
        np.random.uniform(70, 95, n),
        np.random.uniform(30, 69, n)
    )
    return pd.Series(scores, index=sample_price_df.index)


def test_build_signal_series_returns_boolean_series(sample_price_df, sample_signal_scores):
    """build_signal_series harus return boolean Series dengan threshold 70."""
    signals = build_signal_series(sample_signal_scores, threshold=70)
    assert signals.dtype == bool
    assert len(signals) == len(sample_price_df)
    assert signals.sum() > 0


def test_build_signal_series_threshold_filtering(sample_signal_scores):
    """Score di bawah threshold harus False."""
    sample_signal_scores.iloc[0] = 50
    sample_signal_scores.iloc[1] = 75
    signals = build_signal_series(sample_signal_scores, threshold=70)
    assert signals.iloc[0] == False
    assert signals.iloc[1] == True


def test_simulate_trades_returns_valid_structure(sample_price_df, sample_signal_scores):
    """simulate_trades harus return dict dengan key yang dibutuhkan."""
    signals = build_signal_series(sample_signal_scores, threshold=70)
    result  = simulate_trades(
        price_df=sample_price_df,
        signals=signals,
        stop_pct=0.10,
        tp1_pct=0.25,
        fee_pct=0.001,
    )
    required_keys = ["trades", "equity_curve", "total_trades"]
    for key in required_keys:
        assert key in result, f"Missing key: {key}"


def test_simulate_trades_no_infinite_holds(sample_price_df, sample_signal_scores):
    """Semua trade harus ditutup sebelum akhir data."""
    signals = build_signal_series(sample_signal_scores, threshold=70)
    result  = simulate_trades(
        price_df=sample_price_df,
        signals=signals,
        stop_pct=0.10,
        tp1_pct=0.25,
        fee_pct=0.001,
    )
    if not result["trades"].empty:
        assert result["trades"]["exit_price"].notna().all()


def test_calc_metrics_basic(sample_price_df, sample_signal_scores):
    """calc_metrics harus return win_rate, sharpe, avg_r, max_drawdown."""
    signals = build_signal_series(sample_signal_scores, threshold=70)
    result  = simulate_trades(
        price_df=sample_price_df,
        signals=signals,
        stop_pct=0.10,
        tp1_pct=0.25,
        fee_pct=0.001,
    )
    metrics = calc_metrics(result)
    assert "win_rate"     in metrics
    assert "sharpe"       in metrics
    assert "avg_r"        in metrics
    assert "max_drawdown" in metrics
    assert 0 <= metrics["win_rate"] <= 1
    assert metrics["max_drawdown"] <= 0


def test_calc_metrics_empty_trades():
    """Jika tidak ada trade, metrics harus return zero values bukan crash."""
    empty_result = {
        "trades":       pd.DataFrame(columns=["pnl_pct", "r_multiple", "exit_price"]),
        "equity_curve": pd.Series([10000.0]),
        "total_trades": 0,
    }
    metrics = calc_metrics(empty_result)
    assert metrics["win_rate"] == 0.0
    assert metrics["total_trades"] == 0
    assert metrics["sharpe"] == 0.0


def test_should_deploy_good_metrics():
    """Val Sharpe > 0.8 + Win rate > 55% = deploy."""
    from backtesting.walk_forward import should_deploy
    ok, reason = should_deploy(
        {"sharpe": 2.0},
        {"sharpe": 1.2, "win_rate": 0.62}
    )
    assert ok is True
    assert "1.20" in reason


def test_should_deploy_low_sharpe():
    """Val Sharpe < 0.8 = do not deploy."""
    from backtesting.walk_forward import should_deploy
    ok, reason = should_deploy(
        {"sharpe": 2.0},
        {"sharpe": 0.5, "win_rate": 0.65}
    )
    assert ok is False
    assert "0.50" in reason


def test_should_deploy_low_win_rate():
    """Win rate < 55% = do not deploy."""
    from backtesting.walk_forward import should_deploy
    ok, reason = should_deploy(
        {"sharpe": 2.0},
        {"sharpe": 1.0, "win_rate": 0.45}
    )
    assert ok is False
    assert "45.0%" in reason


def test_should_deploy_overfitting_detected():
    """Val Sharpe < 40% of train Sharpe = overfitting."""
    from backtesting.walk_forward import should_deploy
    ok, reason = should_deploy(
        {"sharpe": 3.0},
        {"sharpe": 0.9, "win_rate": 0.65}
    )
    assert ok is False
    assert "overfitting" in reason.lower()
