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


from collector.news import (
    detect_critical_keywords,
    calc_news_modifier,
    get_news_gate,
)


def test_critical_keyword_triggers_block():
    assert detect_critical_keywords("Solana network hacked, $50M stolen") is True
    assert detect_critical_keywords("Solana price reaches new ATH") is False


def test_news_modifier_bearish(db):
    for i in range(3):
        db.upsert_coin_news("ETHUSDT", [{
            "id": f"news-{i}", "title": f"ETH bad news {i}",
            "sentiment": "bearish", "is_critical": False,
            "votes_pos": 0, "votes_neg": 5, "source": "test",
            "published_at": datetime.utcnow() - timedelta(hours=1),
        }])
    mod = calc_news_modifier("ETHUSDT", db)
    assert mod <= -8


def test_engine_blocked_by_news_returns_zero(db):
    db.set_news_block("ARBUSDT", "Arbitrum bridge exploit")
    gate = get_news_gate("ARBUSDT", db)
    assert gate["blocked"] is True
    assert gate["modifier"] == 0


from collector.options import calc_options_modifier, get_options_modifier


def test_options_modifier_fear_market(db):
    """Put/call > 1.3 + skew > 5 harus return modifier negatif."""
    db.upsert_options_metrics("BTCUSDT", {
        "put_call_ratio": 1.5, "skew_25d": 7.0, "iv_atm": 0.8
    })
    mod = get_options_modifier("BTCUSDT", db)
    assert mod <= -5


def test_options_modifier_altcoin_returns_zero(db):
    """Altcoin (bukan BTC/ETH) harus return 0."""
    mod = get_options_modifier("SOLUSDT", db)
    assert mod == 0.0
