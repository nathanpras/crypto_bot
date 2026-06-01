# tests/test_onchain_enhanced.py
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.onchain_enhanced import (
    score_from_futures_data,
    score_from_coinmetrics_data,
    calc_onchain_score_enhanced,
)


def test_score_from_futures_bullish():
    """OI naik + funding negatif + price naik = score tinggi."""
    data = {
        "oi_change_24h_pct": 15.0,
        "funding_rate":      -0.03,
        "long_short_ratio":  0.75,
        "liq_long_24h":      1_000_000,
        "liq_short_24h":     5_000_000,
    }
    score = score_from_futures_data(data)
    assert score >= 75, f"Expected >= 75, got {score}"


def test_score_from_futures_overleveraged():
    """Funding sangat positif = overleveraged longs = score rendah."""
    data = {
        "oi_change_24h_pct": 5.0,
        "funding_rate":      0.08,   # sangat positif
        "long_short_ratio":  2.5,    # terlalu banyak longs
        "liq_long_24h":      100_000,
        "liq_short_24h":     10_000,
    }
    score = score_from_futures_data(data)
    assert score <= 30, f"Expected <= 30, got {score}"


def test_score_from_futures_neutral():
    """Semua data netral = score ~50."""
    data = {
        "oi_change_24h_pct": 0.5,
        "funding_rate":      0.01,
        "long_short_ratio":  1.0,
        "liq_long_24h":      500_000,
        "liq_short_24h":     500_000,
    }
    score = score_from_futures_data(data)
    assert 40 <= score <= 65, f"Expected 40-65, got {score}"


def test_score_from_futures_long_liquidation_cascade():
    """Long liquidation besar = capitulation = potential bottom = bullish."""
    data = {
        "oi_change_24h_pct": -5.0,
        "funding_rate":      0.00,
        "long_short_ratio":  1.0,
        "liq_long_24h":      50_000_000,  # massive long liq
        "liq_short_24h":     1_000_000,
    }
    score = score_from_futures_data(data)
    assert score >= 70, f"Long cascade should be bullish, got {score}"


def test_score_from_coinmetrics_undervalued():
    """MVRV < 1 = undervalued + outflow = sangat bullish."""
    data = {
        "exch_netflow": -5000,   # outflow bullish
        "mvrv_ratio":   0.85,    # undervalued
    }
    score = score_from_coinmetrics_data(data)
    assert score >= 80, f"Expected >= 80, got {score}"


def test_score_from_coinmetrics_overvalued():
    """MVRV > 3 = overvalued + inflow = bearish."""
    data = {
        "exch_netflow": 3000,   # inflow bearish
        "mvrv_ratio":   3.5,    # overvalued
    }
    score = score_from_coinmetrics_data(data)
    assert score <= 25, f"Expected <= 25, got {score}"


def test_calc_onchain_score_enhanced_returns_float():
    """calc_onchain_score_enhanced harus return float 0-100."""
    mock_db = MagicMock()
    mock_db.get_futures_metrics.return_value = pd.DataFrame([{
        "symbol": "SOLUSDT", "timestamp": "2024-01-01",
        "open_interest": 1e9, "oi_change_24h_pct": 10.0,
        "funding_rate": -0.02, "long_short_ratio": 0.9,
        "liq_long_24h": 1e6, "liq_short_24h": 5e5,
    }])

    score = calc_onchain_score_enhanced("SOLUSDT", mock_db)
    assert isinstance(score, float)
    assert 0 <= score <= 100


def test_calc_onchain_score_enhanced_returns_50_when_no_data():
    """Jika tidak ada data futures, return 50 (netral)."""
    mock_db = MagicMock()
    mock_db.get_futures_metrics.return_value = pd.DataFrame()

    score = calc_onchain_score_enhanced("UNKNOWNUSDT", mock_db)
    assert score == 50.0
