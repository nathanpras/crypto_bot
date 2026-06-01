# tests/test_database_phase2.py
import pytest
import sys
from pathlib import Path
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database


@pytest.fixture
def db():
    """Fresh in-memory database untuk setiap test."""
    d = Database(":memory:")
    return d


def test_futures_metrics_table_exists(db):
    result = db.conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='futures_metrics'"
    ).fetchone()[0]
    assert result == 1


def test_sector_tvl_table_exists(db):
    result = db.conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='sector_tvl'"
    ).fetchone()[0]
    assert result == 1


def test_token_unlocks_table_exists(db):
    result = db.conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='token_unlocks'"
    ).fetchone()[0]
    assert result == 1


def test_backtest_results_table_exists(db):
    result = db.conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='backtest_results'"
    ).fetchone()[0]
    assert result == 1


def test_upsert_and_get_futures_metrics(db):
    db.upsert_futures_metrics("SOLUSDT", {
        "open_interest":     500_000_000.0,
        "oi_change_24h_pct": 12.5,
        "funding_rate":      -0.02,
        "long_short_ratio":  0.85,
        "liq_long_24h":      2_000_000.0,
        "liq_short_24h":     500_000.0,
    })
    result = db.get_futures_metrics("SOLUSDT")
    assert not result.empty
    assert result.iloc[0]["funding_rate"] == pytest.approx(-0.02)


def test_upsert_and_get_sector_tvl(db):
    db.upsert_sector_tvl("solana", {
        "tvl_usd":        5_000_000_000.0,
        "tvl_change_7d":  8.3,
        "tvl_change_30d": 22.1,
    })
    result = db.get_sector_tvl("solana")
    assert result["tvl_change_30d"] == pytest.approx(22.1)


def test_upsert_and_get_token_unlock(db):
    db.upsert_token_unlock("ARBUSDT", {
        "unlock_date":       date(2026, 7, 1),
        "unlock_amount_usd": 50_000_000.0,
        "unlock_pct_supply": 3.5,
        "category":          "investor",
    })
    upcoming = db.get_upcoming_unlocks("ARBUSDT", days=60)
    assert len(upcoming) == 1
    assert upcoming[0]["unlock_pct_supply"] == pytest.approx(3.5)


def test_get_upcoming_unlocks_empty_when_none(db):
    result = db.get_upcoming_unlocks("BTCUSDT", days=30)
    assert result == []


def test_save_and_get_backtest_result(db):
    db.save_backtest_result({
        "run_id":         "TEST001",
        "weights_json":   '{"trend":0.20,"rsi":0.15}',
        "train_start":    date(2023, 1, 1),
        "train_end":      date(2024, 6, 30),
        "val_start":      date(2024, 7, 1),
        "val_end":        date(2024, 12, 31),
        "train_win_rate": 68.5,
        "val_win_rate":   65.2,
        "train_sharpe":   1.92,
        "val_sharpe":     1.54,
        "total_trades":   147,
        "avg_r":          2.31,
        "max_drawdown":   -12.3,
        "deployed":       False,
    })
    result = db.get_best_backtest()
    assert result["run_id"] == "TEST001"
    assert result["val_sharpe"] == pytest.approx(1.54)
