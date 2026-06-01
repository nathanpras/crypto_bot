# tests/test_engine_phase2.py
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import date, timedelta
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_db_with_candles():
    """Mock DB yang return candle data cukup untuk scoring."""
    mock = MagicMock()

    n = 220
    np.random.seed(42)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + np.random.normal(0.001, 0.02)))

    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="4h"),
        "open":   [p * 0.999 for p in prices],
        "high":   [p * 1.010 for p in prices],
        "low":    [p * 0.990 for p in prices],
        "close":  prices,
        "volume": [abs(np.random.normal(1_000_000, 200_000)) for _ in range(n)],
    })

    mock.get_candles.return_value = df
    mock.get_futures_metrics.return_value = pd.DataFrame([{
        "symbol": "SOLUSDT", "timestamp": "2024-01-01",
        "open_interest": 1e9, "oi_change_24h_pct": 8.0,
        "funding_rate": -0.015, "long_short_ratio": 0.9,
        "liq_long_24h": 500_000, "liq_short_24h": 1_500_000,
    }])
    mock.get_sector_tvl.return_value = {"tvl_change_30d": 18.5}
    mock.get_upcoming_unlocks.return_value = []
    mock.conn.execute.return_value.df.return_value = pd.DataFrame()
    mock.upsert_signal.return_value = None

    return mock


def test_score_coin_returns_required_fields(mock_db_with_candles):
    """score_coin harus return semua field yang dibutuhkan."""
    from signals.engine import score_coin

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result = score_coin("SOLUSDT", fear_greed=40)

    required = ["symbol", "total_score", "fired", "strong", "signals",
                "price", "regime", "tier"]
    for field in required:
        assert field in result, f"Missing field: {field}"


def test_score_coin_total_score_in_range(mock_db_with_candles):
    """Total score harus dalam range 0-100."""
    from signals.engine import score_coin

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result = score_coin("SOLUSDT", fear_greed=40)

    assert 0 <= result["total_score"] <= 100


def test_score_coin_with_unlock_penalty_reduces_score(mock_db_with_candles):
    """Unlock dalam 7 hari harus kurangi score."""
    from signals.engine import score_coin

    today = date.today()
    mock_db_with_candles.get_upcoming_unlocks.return_value = [{
        "unlock_date":       today + timedelta(days=5),
        "unlock_pct_supply": 2.0,
        "unlock_amount_usd": 10_000_000,
        "category":          "investor",
    }]

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result_with_unlock = score_coin("SOLUSDT", fear_greed=40)

    mock_db_with_candles.get_upcoming_unlocks.return_value = []

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result_without_unlock = score_coin("SOLUSDT", fear_greed=40)

    assert result_with_unlock["total_score"] <= result_without_unlock["total_score"]


def test_score_coin_sector_modifier_appears_in_result(mock_db_with_candles):
    """sector_modifier dan unlock_penalty harus ada di result dict."""
    from signals.engine import score_coin

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result = score_coin("SOLUSDT", fear_greed=40)

    assert "sector_modifier" in result
    assert "unlock_penalty" in result


def test_score_coin_blocked_tier_returns_zero(mock_db_with_candles):
    """Coin yang tiernya tidak allowed harus return score 0."""
    from signals.engine import score_coin

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result = score_coin("SOLUSDT", fear_greed=40, allowed_tiers=[1])

    assert result["total_score"] == 0
    assert result["fired"] == False
