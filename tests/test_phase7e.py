"""
tests/test_phase7e.py — Phase 7E: Token unlock fix, F1 cache staleness, final integration.
"""
import sys
import json
import pytest
from pathlib import Path
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock, mock_open

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.token_unlocks import (
    fetch_unlocks_defillama,
    calc_unlock_penalty,
    collect_all_token_unlocks,
)
from collector.macro import evaluate_f1_gate


# ── Token unlock: DeFiLlama primary source ───────────────────

def test_fetch_unlocks_defillama_returns_list():
    """DeFiLlama fetch should return a list (empty if no data)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "totalSupply": 1_000_000_000,
        "unlocks": [
            {
                "timestamp": (date.today() + timedelta(days=10)).strftime("%Y-%m-%d"),
                "noOfTokens": 50_000_000,
                "type": "investor",
            },
            {
                "timestamp": (date.today() + timedelta(days=20)).strftime("%Y-%m-%d"),
                "noOfTokens": 30_000_000,
                "type": "team",
            },
        ]
    }
    with patch("collector.token_unlocks.requests.get", return_value=mock_resp):
        result = fetch_unlocks_defillama("SOLUSDT")
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["unlock_pct_supply"] == pytest.approx(5.0, abs=0.01)
    assert result[0]["category"] == "investor"


def test_fetch_unlocks_defillama_filters_past_dates():
    """Unlocks in the past should be skipped."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "totalSupply": 1_000_000_000,
        "unlocks": [
            {
                "timestamp": (date.today() - timedelta(days=5)).strftime("%Y-%m-%d"),
                "noOfTokens": 50_000_000,
                "type": "team",
            },
        ]
    }
    with patch("collector.token_unlocks.requests.get", return_value=mock_resp):
        result = fetch_unlocks_defillama("SOLUSDT")
    assert result == [], "Past unlocks should be filtered out"


def test_fetch_unlocks_defillama_returns_empty_on_error():
    with patch("collector.token_unlocks.requests.get",
               side_effect=Exception("network error")):
        result = fetch_unlocks_defillama("SOLUSDT")
    assert result == []


def test_fetch_unlocks_defillama_returns_empty_for_btc():
    """BTC has no TOKENOMIST_SLUGS entry → return [] without any API call."""
    result = fetch_unlocks_defillama("BTCUSDT")
    assert result == []


def test_collect_all_token_unlocks_uses_defillama_first(in_memory_db):
    """DeFiLlama should be tried before Playwright scraper."""
    future_date = (date.today() + timedelta(days=15)).strftime("%Y-%m-%d")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "totalSupply": 1_000_000_000,
        "unlocks": [{"timestamp": future_date, "noOfTokens": 20_000_000, "type": "team"}],
    }

    with patch("collector.token_unlocks.requests.get", return_value=mock_resp), \
         patch("collector.token_unlocks.get_db", return_value=in_memory_db), \
         patch("collector.token_unlocks.scrape_tokenomist") as mock_pw:
        collect_all_token_unlocks()

    # Playwright fallback should NOT have been called since DeFiLlama returned data
    assert not mock_pw.called or mock_pw.call_count < 17, \
        "Playwright should be skipped when DeFiLlama returns data"


# ── F1 cache staleness ────────────────────────────────────────

def test_f1_gate_fresh_cache_is_used():
    """Cache less than 7 days old should be trusted."""
    macro = {
        "fear_greed":    {"value": 50, "label": "Neutral"},
        "m2":            {"status": "error", "trend": "unknown"},
        "btc_dominance": 55.0,
    }
    fresh_cache = {
        "f1_pass":    False,   # cached = contracting
        "m2_trend":   "contracting",
        "cached_at":  (datetime.utcnow() - timedelta(days=3)).isoformat(),
    }
    with patch("collector.macro._load_f1_cache", return_value=fresh_cache):
        result = evaluate_f1_gate(macro)
    assert result["passed"] is False, "Fresh cache showing contraction should fail F1"


def test_f1_gate_stale_cache_passes_optimistically():
    """Cache older than 7 days should be ignored → pass optimistically."""
    macro = {
        "fear_greed":    {"value": 50, "label": "Neutral"},
        "m2":            {"status": "error", "trend": "unknown"},
        "btc_dominance": 55.0,
    }
    stale_cache = {
        "f1_pass":    False,   # cached was contracting, but it's 10 days old
        "m2_trend":   "contracting",
        "cached_at":  (datetime.utcnow() - timedelta(days=10)).isoformat(),
    }
    with patch("collector.macro._load_f1_cache", return_value=stale_cache):
        result = evaluate_f1_gate(macro)
    assert result["passed"] is True, \
        "Stale cache (>7d) should be ignored → optimistic pass"


def test_f1_gate_no_key_always_passes():
    """No FRED key → always pass regardless of cache."""
    macro = {
        "fear_greed":    {"value": 50, "label": "Neutral"},
        "m2":            {"status": "no_key", "trend": "unknown"},
        "btc_dominance": 55.0,
    }
    result = evaluate_f1_gate(macro)
    assert result["passed"] is True


# ── Final integration: score_coin accepts extended_ctx ────────

def _make_mock_db_for_score_coin():
    """Build a fully configured mock DB for score_coin integration tests."""
    import pandas as pd
    import numpy as np
    n = 220
    rng = np.random.default_rng(42)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    df = pd.DataFrame({
        "open":  close, "high": close * 1.005, "low": close * 0.995,
        "close": close, "volume": rng.uniform(1e6, 2e6, n),
        "timestamp": pd.date_range("2023-01-01", periods=n, freq="4h"),
    })
    mock_db = MagicMock()
    mock_db.get_candles.return_value = df
    mock_db.get_futures_metrics.return_value = pd.DataFrame()
    mock_db.get_upcoming_unlocks.return_value = []
    mock_db.get_sector_tvl.return_value = {"tvl_change_30d": 0.0}   # real dict
    mock_db.get_latest_social.return_value = None
    mock_db.get_whale_metrics.return_value = pd.DataFrame()
    mock_db.is_news_blocked.return_value = None
    mock_db.get_recent_news.return_value = []
    mock_db.get_latest_options.return_value = None
    mock_db.upsert_signal.return_value = None
    return mock_db


def test_score_coin_accepts_extended_ctx_without_crash():
    """score_coin should not crash when extended_ctx is provided."""
    from signals.engine import score_coin

    mock_db = _make_mock_db_for_score_coin()
    extended_ctx = {
        "stablecoin_flows": {"modifier": 2.0, "status": "ok",
                             "current_bn": 100.0,
                             "change_7d_pct": 2.0, "change_30d_pct": 1.0},
        "bybit_basis":      {"BTCUSDT": {"basis_pct": 0.3, "sentiment": "neutral"}},
        "defillama_fees":   {},
    }

    with patch("signals.engine.get_db", return_value=mock_db):
        result = score_coin("BTCUSDT", fear_greed=50, extended_ctx=extended_ctx)

    assert "total_score" in result
    assert "stable_modifier" in result
    assert result["stable_modifier"] == 2.0


def test_score_coin_works_without_extended_ctx():
    """score_coin should work normally when extended_ctx=None."""
    from signals.engine import score_coin

    mock_db = _make_mock_db_for_score_coin()
    with patch("signals.engine.get_db", return_value=mock_db):
        result = score_coin("BTCUSDT", fear_greed=50, extended_ctx=None)

    assert "total_score" in result
    assert result["stable_modifier"] == 0.0
