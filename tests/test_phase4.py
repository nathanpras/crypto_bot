import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.duckdb"))


def test_set_and_check_news_block(db):
    db.set_news_block("SOLUSDT", "Solana exploit reported")
    block = db.is_news_blocked("SOLUSDT")
    assert block is not None
    assert "exploit" in block["reason"]


def test_clear_news_block(db):
    db.set_news_block("BTCUSDT", "test reason")
    db.clear_news_block("BTCUSDT")
    assert db.is_news_blocked("BTCUSDT") is None


def test_upsert_and_get_options_metrics(db):
    db.upsert_options_metrics("BTCUSDT", {
        "put_call_ratio": 1.4, "skew_25d": 6.5, "iv_atm": 0.65
    })
    m = db.get_latest_options("BTCUSDT")
    assert m is not None
    assert abs(m["put_call_ratio"] - 1.4) < 0.001
