import pytest
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database


@pytest.fixture
def db(tmp_path):
    """Database in temp dir — tidak sentuh production."""
    return Database(str(tmp_path / "test.duckdb"))


def test_open_trade_creates_record(db):
    trade_id = db.open_journal_trade(
        symbol="SOLUSDT", entry_price=142.34, stop_price=128.10,
        tp1_price=177.93, tp2_price=213.51,
        signal_score=87.0, signal_id="SOL-0602",
    )
    assert trade_id is not None
    trade = db.get_open_journal_trade_by_symbol("SOLUSDT")
    assert trade is not None
    assert trade["symbol"] == "SOLUSDT"
    assert trade["entry_price"] == 142.34
    assert trade["status"] == "open"


def test_auto_link_signal_within_48h(db):
    db.upsert_signal("BTCUSDT", {
        "total_score": 85.0,
        "trend_score": 80, "rsi_score": 75, "macd_score": 70,
        "volume_score": 80, "wyckoff_score": 75,
        "onchain_score": 80, "sentiment_score": 70,
        "regime": "TRENDING_BULL",
    }, timestamp=datetime.utcnow() - timedelta(hours=3))
    sig = db.get_last_signal_for_symbol("BTCUSDT", within_hours=48)
    assert sig is not None
    assert sig["total_score"] == 85.0
    assert "signal_id" in sig


def test_auto_link_returns_null_if_no_signal(db):
    sig = db.get_last_signal_for_symbol("ETHUSDT", within_hours=48)
    assert sig is None
