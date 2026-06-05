# tests/test_phase8b.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from signals.registry import SIGNAL_REGISTRY, get_signal_ids, populate_registry_to_db
from database import Database

@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()

def test_registry_has_32_signals():
    assert len(SIGNAL_REGISTRY) == 32

def test_registry_signal_ids_unique():
    ids = [s["id"] for s in SIGNAL_REGISTRY]
    assert len(ids) == len(set(ids)), "Signal IDs must be unique"

def test_registry_all_have_required_fields():
    required = {"id", "name", "category", "update_freq", "source"}
    for sig in SIGNAL_REGISTRY:
        missing = required - sig.keys()
        assert not missing, f"Signal {sig.get('id')} missing fields: {missing}"

def test_registry_categories():
    cats = {s["category"] for s in SIGNAL_REGISTRY}
    assert cats == {"TECHNICAL", "ON_CHAIN", "SENTIMENT", "DERIVATIVES", "MACRO"}

def test_registry_technical_count():
    tech = [s for s in SIGNAL_REGISTRY if s["category"] == "TECHNICAL"]
    assert len(tech) == 10

def test_registry_onchain_count():
    oc = [s for s in SIGNAL_REGISTRY if s["category"] == "ON_CHAIN"]
    assert len(oc) == 7

def test_registry_sentiment_count():
    sent = [s for s in SIGNAL_REGISTRY if s["category"] == "SENTIMENT"]
    assert len(sent) == 6

def test_registry_derivatives_count():
    der = [s for s in SIGNAL_REGISTRY if s["category"] == "DERIVATIVES"]
    assert len(der) == 4

def test_registry_macro_count():
    macro = [s for s in SIGNAL_REGISTRY if s["category"] == "MACRO"]
    assert len(macro) == 5

def test_get_signal_ids_returns_32_strings():
    ids = get_signal_ids()
    assert len(ids) == 32
    assert all(isinstance(i, str) for i in ids)

def test_populate_registry_to_db_inserts_rows(db):
    populate_registry_to_db(db)
    count = db.conn.execute("SELECT COUNT(*) FROM signal_registry").fetchone()[0]
    assert count == 32

def test_registry_enabled_by_default():
    for sig in SIGNAL_REGISTRY:
        assert sig.get("enabled", True) is True


# ── Phase 8B: technical.py function migration tests ──────────

import pandas as pd
import numpy as np

def make_candles(n=220, trend="bull"):
    np.random.seed(42)
    price = 100.0
    prices = []
    for i in range(n):
        drift = 0.003 if trend == "bull" else -0.003
        price *= (1 + np.random.normal(drift, 0.015))
        price = max(price, 1.0)
        prices.append(price)
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="4h"),
        "open":   [p * 0.999 for p in prices],
        "high":   [p * 1.008 for p in prices],
        "low":    [p * 0.992 for p in prices],
        "close":  prices,
        "volume": [abs(np.random.normal(1_000_000, 200_000)) for _ in range(n)],
    })

from signals.technical import (
    calc_trend_score, calc_rsi_score, calc_macd_score,
    calc_volume_score, calc_wyckoff_score,
    calc_vwap_normalized, calc_volume_delta_normalized, calc_bb_squeeze_normalized,
)

from signals.normalizer import get_all_signals

def make_db_with_candles():
    """Helper: DB with 220 candles for BTCUSDT."""
    import numpy as np
    import pandas as pd
    db = Database(":memory:")
    n = 220
    np.random.seed(7)
    price = 30000.0
    rows = []
    for i in range(n):
        price *= (1 + np.random.normal(0.001, 0.018))
        rows.append({
            "symbol": "BTCUSDT", "timeframe": "4h",
            "timestamp": pd.Timestamp("2024-01-01") + pd.Timedelta(hours=4*i),
            "open": price*0.999, "high": price*1.007, "low": price*0.993,
            "close": price, "volume": abs(np.random.normal(800_000_000, 100_000_000)),
        })
    df = pd.DataFrame(rows)
    db.upsert_candles("BTCUSDT", "4h", df)
    rows_1d = rows[::6]
    df_1d = pd.DataFrame([{**r, "timeframe": "1d"} for r in rows_1d])
    db.upsert_candles("BTCUSDT", "1d", df_1d)
    return db

def test_get_all_signals_returns_32_keys():
    db = make_db_with_candles()
    scores = get_all_signals("BTCUSDT", db, fear_greed=50, funding_rate=0.0)
    db.close()
    from signals.registry import get_signal_ids
    expected_ids = set(get_signal_ids())
    assert set(scores.keys()) == expected_ids

def test_get_all_signals_all_in_range():
    db = make_db_with_candles()
    scores = get_all_signals("BTCUSDT", db, fear_greed=50)
    db.close()
    for sid, val in scores.items():
        assert 0.0 <= val <= 100.0, f"Signal {sid} = {val} out of 0-100 range"

def test_get_all_signals_missing_data_returns_neutral():
    db = Database(":memory:")
    scores = get_all_signals("BTCUSDT", db, fear_greed=50)
    db.close()
    for sid in ["T1", "T2", "T3", "T4", "T5"]:
        assert scores[sid] == 50.0, f"No-data {sid} should be 50.0, got {scores[sid]}"

def test_get_all_signals_s1_fear_greed_extreme_fear():
    db = Database(":memory:")
    scores = get_all_signals("BTCUSDT", db, fear_greed=10)
    db.close()
    assert scores["S1"] > 70, "Extreme fear should give high S1 score (contrarian bullish)"

def test_get_all_signals_s1_extreme_greed():
    db = Database(":memory:")
    scores = get_all_signals("BTCUSDT", db, fear_greed=90)
    db.close()
    assert scores["S1"] < 30, "Extreme greed should give low S1 score (contrarian bearish)"

def test_get_all_signals_non_btc_onchain_neutral():
    db = Database(":memory:")
    scores = get_all_signals("SOLUSDT", db, fear_greed=50)
    db.close()
    assert scores["O1"] == 50.0, "MVRV not available for SOLUSDT"
    assert scores["O3"] == 50.0, "BTC on-chain not applicable to SOLUSDT"

def test_calc_trend_score_returns_0_100():
    df = make_candles(220, "bull")
    score = calc_trend_score(df, pd.DataFrame())
    assert 0 <= score <= 100

def test_calc_rsi_score_returns_0_100():
    df = make_candles(50)
    score = calc_rsi_score(df)
    assert 0 <= score <= 100

def test_calc_macd_score_returns_0_100():
    df = make_candles(60)
    score = calc_macd_score(df)
    assert 0 <= score <= 100

def test_calc_volume_score_returns_0_100():
    df = make_candles(30)
    score = calc_volume_score(df)
    assert 0 <= score <= 100

def test_calc_wyckoff_score_returns_0_100():
    df = make_candles(70)
    score = calc_wyckoff_score(df)
    assert 0 <= score <= 100

def test_vwap_normalized_returns_0_100():
    df = make_candles(30)
    score = calc_vwap_normalized(df)
    assert 0 <= score <= 100

def test_volume_delta_normalized_returns_0_100():
    df = make_candles(20)
    score = calc_volume_delta_normalized(df)
    assert 0 <= score <= 100

def test_bb_squeeze_normalized_returns_0_100():
    df = make_candles(30)
    score = calc_bb_squeeze_normalized(df)
    assert 0 <= score <= 100
