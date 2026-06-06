"""
tests/test_phase7d.py — Phase 7D: Stablecoin flows, FRED expansion, Bybit basis, DeFiLlama fees.
All external HTTP calls are mocked.
"""
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.macro_extended import (
    fetch_stablecoin_flows,
    fetch_fred_extended,
    fetch_bybit_basis,
    fetch_defillama_fees,
    get_basis_modifier,
    get_fees_ecosystem_score,
)
from config import STABLECOIN_THRESHOLDS, BYBIT_BASIS_SYMBOLS


# ── Stablecoin flows ──────────────────────────────────────────

def _mock_stable_response(total_now, total_7d_ago, total_30d_ago=None):
    """Build mock responses for stablecoin API."""
    mock1 = MagicMock()
    mock1.status_code = 200
    mock1.json.return_value = {
        "peggedAssets": [
            {"name": "Tether", "gecko_id": "tether",
             "circulating": {"peggedUSD": total_now}},
        ]
    }
    mock2 = MagicMock()
    mock2.status_code = 200
    mock2.json.return_value = [
        {"total7dAgo": total_7d_ago,
         "total1mAgo": total_30d_ago or total_7d_ago * 0.95}
    ]
    return [mock1, mock2]


def test_stablecoin_flows_rising_supply_bullish():
    side_effects = _mock_stable_response(
        total_now=100e9, total_7d_ago=96e9  # +4.2% → modifier = +5
    )
    with patch("collector.macro_extended.requests.get",
               side_effect=side_effects):
        result = fetch_stablecoin_flows()
    assert result["modifier"] > 0, f"Rising supply should be bullish, got {result}"
    assert result["status"] == "ok"


def test_stablecoin_flows_falling_supply_bearish():
    side_effects = _mock_stable_response(
        total_now=95e9, total_7d_ago=100e9  # -5% → modifier = -5
    )
    with patch("collector.macro_extended.requests.get",
               side_effect=side_effects):
        result = fetch_stablecoin_flows()
    assert result["modifier"] < 0, f"Falling supply should be bearish, got {result}"


def test_stablecoin_flows_error_returns_zero_modifier():
    with patch("collector.macro_extended.requests.get",
               side_effect=Exception("network error")):
        result = fetch_stablecoin_flows()
    assert result["modifier"] == 0.0
    assert result["status"] == "error"


def test_stablecoin_config_thresholds_defined():
    assert "strong_inflow" in STABLECOIN_THRESHOLDS
    assert STABLECOIN_THRESHOLDS["strong_inflow"] > 0
    assert STABLECOIN_THRESHOLDS["strong_outflow"] < 0


# ── FRED extended ─────────────────────────────────────────────

def test_fred_extended_no_key_returns_unknown():
    with patch("collector.macro_extended.FRED_KEY", ""):
        result = fetch_fred_extended()
    assert result["cpi"]["trend"] == "unknown"
    assert result["yield_10y"]["trend"] == "unknown"


def test_fred_extended_cpi_falling_is_favorable():
    def mock_fred(url, params=None, timeout=None):
        series = params.get("series_id", "")
        m = MagicMock()
        m.status_code = 200
        if series == "CPIAUCSL":
            m.json.return_value = {"observations": [
                {"value": "310.0"}, {"value": "311.0"},
                {"value": "312.0"}, {"value": "313.0"},
            ]}
        elif series == "GS10":
            m.json.return_value = {"observations": [
                {"value": "4.2"}, {"value": "4.5"},
                {"value": "4.7"}, {"value": "4.8"},
            ]}
        else:
            m.json.return_value = {"observations": [{"value": "1.3"}]}
        m.raise_for_status = lambda: None
        return m

    with patch("collector.macro_extended.FRED_KEY", "test_key"), \
         patch("collector.macro_extended.requests.get", side_effect=mock_fred):
        result = fetch_fred_extended()

    assert result["cpi"]["trend"] == "falling"
    assert result["yield_10y"]["trend"] == "falling"
    assert result["macro_favorable"] is True


# ── Bybit basis ───────────────────────────────────────────────

def _mock_bybit_ticker(last_price, mark_price, index_price):
    m = MagicMock()
    m.json.return_value = {"result": {"list": [{
        "lastPrice":   str(last_price),
        "markPrice":   str(mark_price),
        "indexPrice":  str(index_price),
    }]}}
    return m


def test_bybit_basis_positive_is_bullish():
    mock_resp = _mock_bybit_ticker(50500, 50450, 50000)  # basis ≈ +1%
    with patch("collector.macro_extended.requests.get", return_value=mock_resp):
        result = fetch_bybit_basis(["BTCUSDT"])
    assert "BTCUSDT" in result
    assert result["BTCUSDT"]["sentiment"] == "bullish"
    assert result["BTCUSDT"]["basis_pct"] > 0


def test_bybit_basis_negative_is_bearish():
    mock_resp = _mock_bybit_ticker(49500, 49550, 50000)  # basis ≈ -1%
    with patch("collector.macro_extended.requests.get", return_value=mock_resp):
        result = fetch_bybit_basis(["BTCUSDT"])
    assert result["BTCUSDT"]["sentiment"] == "bearish"
    assert result["BTCUSDT"]["basis_pct"] < 0


def test_get_basis_modifier_range():
    basis_data = {
        "BTCUSDT": {"basis_pct": 1.0, "sentiment": "bullish"},
        "ETHUSDT": {"basis_pct": -1.5, "sentiment": "bearish"},
    }
    mod_btc = get_basis_modifier("BTCUSDT", basis_data)
    mod_eth = get_basis_modifier("ETHUSDT", basis_data)
    assert -5 <= mod_btc <= 5
    assert -5 <= mod_eth <= 5
    assert mod_btc > 0 and mod_eth < 0


def test_bybit_basis_symbols_config():
    assert "BTCUSDT" in BYBIT_BASIS_SYMBOLS
    assert "ETHUSDT" in BYBIT_BASIS_SYMBOLS


# ── DeFiLlama fees ────────────────────────────────────────────

def test_defillama_fees_returns_dict():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"protocols": [
        {"name": "Uniswap",     "total24h": 1_000_000, "total48hto24h": 900_000},
        {"name": "PancakeSwap", "total24h": 500_000,   "total48hto24h": 600_000},
    ]}
    mock_resp.raise_for_status = lambda: None
    with patch("collector.macro_extended.requests.get", return_value=mock_resp):
        result = fetch_defillama_fees()
    assert "Uniswap" in result
    assert result["Uniswap"]["daily_fees_usd"] == 1_000_000
    assert result["Uniswap"]["change_1d_pct"] > 0   # grew


def test_defillama_fees_error_returns_empty():
    with patch("collector.macro_extended.requests.get",
               side_effect=Exception("timeout")):
        result = fetch_defillama_fees()
    assert result == {}


def test_get_fees_ecosystem_score_positive():
    fees_data = {
        "Uniswap": {"daily_fees_usd": 1e6, "change_1d_pct": 25.0},
    }
    score = get_fees_ecosystem_score("ethereum", fees_data)
    assert score > 0, f"High fee growth should give positive score, got {score}"


def test_get_fees_ecosystem_score_unknown_chain():
    fees_data = {"Uniswap": {"daily_fees_usd": 1e6, "change_1d_pct": 25.0}}
    score = get_fees_ecosystem_score("unknown_chain", fees_data)
    assert score == 0.0


def test_get_fees_ecosystem_score_range():
    fees_data = {
        "Uniswap":     {"daily_fees_usd": 1e6, "change_1d_pct": 50.0},
        "PancakeSwap": {"daily_fees_usd": 1e5, "change_1d_pct": -30.0},
    }
    for chain in ["ethereum", "bsc", "unknown"]:
        score = get_fees_ecosystem_score(chain, fees_data)
        assert -3 <= score <= 3, f"Score out of range for {chain}: {score}"
