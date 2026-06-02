import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta, date

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.duckdb"))


def test_upsert_and_get_social_metrics(db):
    db.upsert_social_metrics("SOLUSDT", {
        "twitter_followers": 500000, "twitter_change_30d": 8.5,
        "reddit_subscribers": 120000, "reddit_change_30d": 3.2,
        "github_commits_4w": 85, "telegram_members": 45000,
        "social_score": 6.0,
    })
    m = db.get_latest_social("SOLUSDT")
    assert m is not None
    assert m["twitter_followers"] == 500000
    assert abs(m["twitter_change_30d"] - 8.5) < 0.01


def test_get_latest_social_returns_none_if_no_data(db):
    assert db.get_latest_social("XRPUSDT") is None


from collector.social import calc_social_score, get_social_modifier


def test_calc_social_score_bullish():
    score = calc_social_score(
        twitter_change_30d=7.0,
        reddit_change_30d=5.0,
        github_commits_4w=80,
    )
    assert score > 0


def test_calc_social_score_bearish():
    score = calc_social_score(
        twitter_change_30d=-7.0,
        reddit_change_30d=-5.0,
        github_commits_4w=0,
    )
    assert score < 0


def test_get_social_modifier_no_data_returns_zero(db):
    mod = get_social_modifier("DOTUSDT", db)
    assert mod == 0.0


from collector.whale import calc_whale_score, get_whale_modifier


def test_whale_score_btc_accumulation(db):
    """BTC net outflow = accumulation = positive score."""
    from datetime import date, timedelta
    for i in range(7):
        d = date.today() - timedelta(days=i)
        db.conn.execute("""
            INSERT OR REPLACE INTO onchain
                (asset, date, exch_inflow, exch_outflow, exch_netflow,
                 mvrv_ratio, nupl, active_addr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ["BTC", d, 500, 800, -300, 1.5, 0.4, 900000])
    score = calc_whale_score("BTCUSDT", db)
    assert score > 0


def test_whale_modifier_altcoin_no_data_returns_zero(db):
    """Altcoin without futures_metrics data returns 0."""
    mod = get_whale_modifier("NEARUSDT", db)
    assert mod == 0.0


def test_whale_score_btc_distribution(db):
    """BTC net inflow = distribution = negative score."""
    from datetime import date, timedelta
    for i in range(7):
        d = date.today() - timedelta(days=i)
        db.conn.execute("""
            INSERT OR REPLACE INTO onchain
                (asset, date, exch_inflow, exch_outflow, exch_netflow,
                 mvrv_ratio, nupl, active_addr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ["BTC", d, 2000, 500, 1500, 2.5, 0.7, 850000])
    score = calc_whale_score("BTCUSDT", db)
    assert score < 0
