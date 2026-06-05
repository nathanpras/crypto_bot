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

def test_get_onchain_real_history(db):
    from datetime import date, timedelta
    for i in range(5):
        db.upsert_onchain_real("BTC", {
            "date": date.today() - timedelta(days=i),
            "active_addr": 900_000 + i * 1000,
            "tx_count": 300_000 + i * 500,
            "exchange_inflow": 1000.0,
            "exchange_outflow": 1000.0,
            "nvt_ratio": 40.0,
        })
    rows = db.get_onchain_real_history("BTC", days=5)
    assert len(rows) == 5
    assert all("tx_count" in r for r in rows)

def test_get_funding_30d_ma(db):
    from datetime import datetime, timedelta
    for i in range(10):
        db.upsert_funding_history("BTCUSDT", {
            "timestamp": datetime.utcnow() - timedelta(hours=i * 8),
            "funding_rate": 0.0003,
        })
    ma = db.get_funding_30d_ma("BTCUSDT")
    assert abs(ma - 0.0003) < 1e-6

from unittest.mock import patch, MagicMock
from collector.liquidations import fetch_liquidation_cascade, get_liquidation_cascade_score

def _mock_coinglass(long_usd: float, short_usd: float):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {
        "code": "0",
        "data": [
            {"longLiqUsd": str(long_usd / 24), "shortLiqUsd": str(short_usd / 24)}
            for _ in range(24)
        ]
    }
    m.raise_for_status = lambda: None
    return m

def test_fetch_liquidation_cascade_no_key(monkeypatch):
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    result = fetch_liquidation_cascade("BTCUSDT")
    assert result == {}

def test_fetch_liquidation_cascade_short_squeeze(monkeypatch):
    monkeypatch.setenv("COINGLASS_API_KEY", "test_key")
    with patch("collector.liquidations.requests.get",
               return_value=_mock_coinglass(2_000_000, 10_000_000)):
        result = fetch_liquidation_cascade("BTCUSDT")
    assert result["liq_short_24h"] == pytest.approx(10_000_000, rel=0.01)
    assert result["liq_long_24h"] == pytest.approx(2_000_000, rel=0.01)

def test_fetch_liquidation_cascade_api_error(monkeypatch):
    monkeypatch.setenv("COINGLASS_API_KEY", "test_key")
    m = MagicMock()
    m.raise_for_status = lambda: None
    m.json.return_value = {"code": "1", "msg": "rate limit"}
    with patch("collector.liquidations.requests.get", return_value=m):
        result = fetch_liquidation_cascade("BTCUSDT")
    assert result == {}

def test_liquidation_cascade_score_short_squeeze(db):
    from datetime import datetime
    db.upsert_liquidation("BTCUSDT", {"liq_long_usd": 1_000_000, "liq_short_usd": 9_000_000, "timestamp": datetime.utcnow()})
    score = get_liquidation_cascade_score("BTCUSDT", db)
    assert score > 70, "Short squeeze (90% short liq) should score > 70"

def test_liquidation_cascade_score_long_wipeout(db):
    from datetime import datetime
    db.upsert_liquidation("BTCUSDT", {"liq_long_usd": 9_000_000, "liq_short_usd": 1_000_000, "timestamp": datetime.utcnow()})
    score = get_liquidation_cascade_score("BTCUSDT", db)
    assert score < 30, "Long wipeout (90% long liq) should score < 30"

def test_liquidation_cascade_score_no_data(db):
    score = get_liquidation_cascade_score("SOLUSDT", db)
    assert score == 50.0, "Missing data should return neutral 50"

from collector.onchain_real import (
    fetch_btc_onchain, fetch_eth_onchain,
    get_real_onchain_score, compute_nvt_score,
)

def _mock_blockchain_info():
    stats = MagicMock()
    stats.status_code = 200
    stats.json.return_value = {
        "n_unique_addresses": 950_000,
        "n_tx": 350_000,
    }
    stats.raise_for_status = lambda: None
    vol = MagicMock()
    vol.status_code = 200
    vol.json.return_value = {"values": [{"y": 8_500_000_000}]}
    vol.raise_for_status = lambda: None
    return [stats, vol]

def test_fetch_btc_onchain_parses_active_addresses():
    with patch("collector.onchain_real.requests.get",
               side_effect=_mock_blockchain_info()):
        result = fetch_btc_onchain()
    assert result["asset"] == "BTC"
    assert result["active_addr"] == 950_000
    assert result["tx_count"] == 350_000

def test_fetch_btc_onchain_handles_api_failure():
    with patch("collector.onchain_real.requests.get",
               side_effect=Exception("network error")):
        result = fetch_btc_onchain()
    assert result == {}

def test_fetch_eth_onchain_no_key(monkeypatch):
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    result = fetch_eth_onchain()
    assert result == {}

def test_fetch_eth_onchain_with_key(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test_key")
    m = MagicMock()
    m.status_code = 200
    m.raise_for_status = lambda: None
    m.json.return_value = {
        "status": "1",
        "result": [{"uniqTxsCount": "420000"}],
    }
    with patch("collector.onchain_real.requests.get", return_value=m):
        result = fetch_eth_onchain()
    assert result["asset"] == "ETH"
    assert result["active_addr"] == 0
    assert result["tx_count"] == 420_000

def test_real_onchain_score_neutral_when_no_data(db):
    score = get_real_onchain_score("BTCUSDT", db)
    assert score == 50.0

def test_real_onchain_score_non_btc_eth_neutral(db):
    score = get_real_onchain_score("SOLUSDT", db)
    assert score == 50.0

def test_compute_nvt_score_rising_activity_bullish(db):
    from datetime import date, timedelta
    # Seed 14 days: recent 7 days have higher tx count than older 7 days
    for i in range(14):
        tx_count = 500_000 if i < 7 else 300_000  # i=0 is most recent
        db.upsert_onchain_real("BTC", {
            "date": date.today() - timedelta(days=i),
            "active_addr": 900_000,
            "tx_count": tx_count,
            "exchange_inflow": 1000.0,
            "exchange_outflow": 1000.0,
            "nvt_ratio": 40.0,
        })
    score = compute_nvt_score("BTC", db)
    assert score > 55, f"Rising tx activity should score above neutral, got {score}"


def test_collect_all_onchain_real_returns_dict(db):
    from collector.onchain_real import collect_all_onchain_real
    with patch("collector.onchain_real.requests.get",
               side_effect=Exception("network error")):
        results = collect_all_onchain_real(db)
    assert isinstance(results, dict)
    assert "BTC" in results
    assert "ETH" in results
    assert results["BTC"] == "skipped"

from collector.social_lunar import (
    fetch_lunarcrush, fetch_reddit_sentiment,
    get_lunarcrush_score, get_google_trends_score, get_reddit_sentiment_score,
)

def test_fetch_lunarcrush_no_key(monkeypatch):
    monkeypatch.delenv("LUNARCRUSH_API_KEY", raising=False)
    result = fetch_lunarcrush("BTCUSDT")
    assert result == {}

def test_fetch_lunarcrush_parses_galaxy_score(monkeypatch):
    monkeypatch.setenv("LUNARCRUSH_API_KEY", "test_key")
    m = MagicMock()
    m.raise_for_status = lambda: None
    m.json.return_value = {
        "data": {"galaxy_score": 68.5, "alt_rank": 5, "social_volume_24h": 15000}
    }
    with patch("collector.social_lunar.requests.get", return_value=m):
        result = fetch_lunarcrush("BTCUSDT")
    assert result["galaxy_score"] == pytest.approx(68.5)
    assert result["alt_rank"] == 5

def test_fetch_lunarcrush_api_error_returns_empty(monkeypatch):
    monkeypatch.setenv("LUNARCRUSH_API_KEY", "test_key")
    with patch("collector.social_lunar.requests.get",
               side_effect=Exception("timeout")):
        result = fetch_lunarcrush("BTCUSDT")
    assert result == {}

def test_lunarcrush_score_high_galaxy_bullish(db):
    from datetime import datetime
    db.upsert_lunarcrush("BTCUSDT", {"galaxy_score": 80.0, "alt_rank": 2, "social_volume": 50000, "timestamp": datetime.utcnow()})
    score = get_lunarcrush_score("BTCUSDT", db)
    assert score > 70

def test_lunarcrush_score_low_galaxy_bearish(db):
    from datetime import datetime
    db.upsert_lunarcrush("BTCUSDT", {"galaxy_score": 20.0, "alt_rank": 95, "social_volume": 2000, "timestamp": datetime.utcnow()})
    score = get_lunarcrush_score("BTCUSDT", db)
    assert score < 40

def test_lunarcrush_score_no_data_neutral(db):
    score = get_lunarcrush_score("NEARUSDT", db)
    assert score == 50.0

def test_reddit_sentiment_score_bullish(db):
    from datetime import date
    db.upsert_reddit_sentiment("BTCUSDT", {
        "date": date.today(), "post_count": 200,
        "avg_sentiment": 0.45, "bullish_pct": 72.0,
    })
    score = get_reddit_sentiment_score("BTCUSDT", db)
    assert score > 60

def test_reddit_sentiment_score_bearish(db):
    from datetime import date
    db.upsert_reddit_sentiment("BTCUSDT", {
        "date": date.today(), "post_count": 200,
        "avg_sentiment": -0.40, "bullish_pct": 22.0,
    })
    score = get_reddit_sentiment_score("BTCUSDT", db)
    assert score < 40

def test_google_trends_score_high_interest_bullish(db):
    from datetime import date
    db.upsert_google_trends("BTCUSDT", {"date": date.today(), "interest": 90})
    score = get_google_trends_score("BTCUSDT", db)
    assert score > 70

def test_google_trends_score_no_data_neutral(db):
    score = get_google_trends_score("INJUSDT", db)
    assert score == 50.0

from collector.orderbook import fetch_orderbook_imbalance, get_orderbook_score
from collector.funding_history import fetch_funding_history, get_funding_oscillator_score

def test_orderbook_score_bid_heavy_bullish():
    score = get_orderbook_score("BTCUSDT", 1.8)
    assert score > 60

def test_orderbook_score_ask_heavy_bearish():
    score = get_orderbook_score("BTCUSDT", 0.4)
    assert score < 40

def test_orderbook_score_balanced():
    score = get_orderbook_score("BTCUSDT", 1.0)
    assert 45 <= score <= 55

def test_fetch_funding_history_no_key(monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    result = fetch_funding_history("BTCUSDT")
    assert result == []

def test_funding_oscillator_score_negative_funding_bullish(db):
    from datetime import datetime, timedelta
    # Seed 28 records starting 8h ago (i=1..28) so the most-recent slot is free
    for i in range(1, 29):
        db.upsert_funding_history("BTCUSDT", {
            "timestamp": datetime.utcnow() - timedelta(hours=i * 8),
            "funding_rate": 0.0003,
        })
    # Insert the current-period record (most recent) with negative funding
    db.upsert_funding_history("BTCUSDT", {
        "timestamp": datetime.utcnow() - timedelta(minutes=5),
        "funding_rate": -0.005,
    })
    score = get_funding_oscillator_score("BTCUSDT", db)
    assert score > 55, f"Negative funding vs positive MA should be bullish, got {score}"

def test_funding_oscillator_score_very_positive_bearish(db):
    from datetime import datetime, timedelta
    for i in range(28):
        db.upsert_funding_history("BTCUSDT2", {
            "timestamp": datetime.utcnow() - timedelta(hours=i * 8 + 2),
            "funding_rate": 0.0001,
        })
    db.upsert_funding_history("BTCUSDT2", {
        "timestamp": datetime.utcnow() - timedelta(hours=1),
        "funding_rate": 0.012,
    })
    score = get_funding_oscillator_score("BTCUSDT2", db)
    assert score < 45, f"Very high funding vs low MA should be bearish, got {score}"
