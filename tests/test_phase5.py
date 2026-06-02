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
