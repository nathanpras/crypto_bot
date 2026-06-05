# tests/test_phase8a.py
import sys
from pathlib import Path
from datetime import datetime, date
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from database import Database

@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()

# ── Schema existence tests ────────────────────────────

def test_signal_registry_table_exists(db):
    tables = [r[0] for r in db.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()]
    assert "signal_registry" in tables

def test_optimized_weights_table_exists(db):
    tables = [r[0] for r in db.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()]
    assert "optimized_weights" in tables

def test_liquidations_table_exists(db):
    tables = [r[0] for r in db.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()]
    assert "liquidations" in tables

def test_onchain_real_table_exists(db):
    tables = [r[0] for r in db.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()]
    assert "onchain_real" in tables

def test_lunarcrush_metrics_table_exists(db):
    tables = [r[0] for r in db.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()]
    assert "lunarcrush_metrics" in tables

def test_google_trends_table_exists(db):
    tables = [r[0] for r in db.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()]
    assert "google_trends" in tables

def test_reddit_sentiment_table_exists(db):
    tables = [r[0] for r in db.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()]
    assert "reddit_sentiment" in tables

def test_funding_history_table_exists(db):
    tables = [r[0] for r in db.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()]
    assert "funding_history" in tables

# ── CRUD tests ────────────────────────────────────────

def test_upsert_and_get_liquidation(db):
    db.upsert_liquidation("BTCUSDT", {
        "liq_long_usd": 5_000_000.0,
        "liq_short_usd": 8_000_000.0,
    })
    row = db.get_latest_liquidation("BTCUSDT")
    assert row is not None
    assert row["liq_long_usd"] == pytest.approx(5_000_000.0)
    assert row["liq_short_usd"] == pytest.approx(8_000_000.0)

def test_upsert_and_get_onchain_real(db):
    db.upsert_onchain_real("BTC", {
        "date": date.today(),
        "active_addr": 950_000,
        "tx_count": 350_000,
        "exchange_inflow": 1200.0,
        "exchange_outflow": 2100.0,
        "nvt_ratio": 45.2,
    })
    row = db.get_latest_onchain_real("BTC")
    assert row is not None
    assert row["active_addr"] == 950_000
    assert row["nvt_ratio"] == pytest.approx(45.2)

def test_upsert_and_get_lunarcrush(db):
    db.upsert_lunarcrush("BTCUSDT", {
        "galaxy_score": 72.5,
        "alt_rank": 3,
        "social_volume": 42000.0,
    })
    row = db.get_latest_lunarcrush("BTCUSDT")
    assert row is not None
    assert row["galaxy_score"] == pytest.approx(72.5)

def test_upsert_and_get_google_trends(db):
    db.upsert_google_trends("BTCUSDT", {
        "date": date.today(),
        "interest": 78,
    })
    row = db.get_latest_google_trends("BTCUSDT")
    assert row is not None
    assert row["interest"] == 78

def test_upsert_and_get_reddit_sentiment(db):
    db.upsert_reddit_sentiment("BTCUSDT", {
        "date": date.today(),
        "post_count": 120,
        "avg_sentiment": 0.32,
        "bullish_pct": 65.0,
    })
    row = db.get_latest_reddit_sentiment("BTCUSDT")
    assert row is not None
    assert row["avg_sentiment"] == pytest.approx(0.32)

def test_upsert_and_get_funding_history(db):
    db.upsert_funding_history("BTCUSDT", {
        "timestamp": datetime(2025, 1, 1, 8, 0),
        "funding_rate": 0.0003,
    })
    rows = db.get_funding_history("BTCUSDT", limit=1)
    assert len(rows) == 1
    assert rows[0]["funding_rate"] == pytest.approx(0.0003)

def test_save_and_get_optimized_weights(db):
    weights = {
        "T1": 0.12, "T2": 0.07, "T3": 0.07, "T4": 0.07, "T5": 0.07,
        "T6": 0.05, "T7": 0.05, "T8": 0.04, "T9": 0.03, "T10": 0.01,
        "O1": 0.05, "O2": 0.04, "O3": 0.03, "O4": 0.02, "O5": 0.04,
        "O6": 0.01, "O7": 0.01, "S1": 0.03, "S2": 0.03, "S3": 0.01,
        "S4": 0.01, "S5": 0.005, "S6": 0.005, "D1": 0.04, "D2": 0.02,
        "D3": 0.01, "D4": 0.01, "M1": 0.02, "M2": 0.01, "M3": 0.01,
        "M4": 0.005, "M5": 0.005,
    }
    db.save_optimized_weights("TRENDING_BULL", weights, fitness_score=1.62)
    loaded = db.get_optimized_weights("TRENDING_BULL")
    assert loaded is not None
    assert abs(loaded.get("T1", 0) - 0.12) < 1e-6
    assert abs(sum(loaded.values()) - 1.0) < 1e-4
