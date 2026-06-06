"""
tests/test_phase7b.py — Phase 7B: VADER NLP sentiment + 10 RSS sources.
"""
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.news import classify_sentiment, build_db_item, calc_news_modifier
from config import RSS_FEEDS, VADER_THRESHOLDS
from datetime import datetime


# ── VADER classify_sentiment ─────────────────────────────────

def test_vader_classifies_bullish_headline():
    label, compound = classify_sentiment("Bitcoin surges to new all-time high as ETF approved")
    assert label == "bullish", f"Expected bullish, got {label} (compound={compound})"
    assert compound > 0


def test_vader_classifies_bearish_headline():
    label, compound = classify_sentiment("Crypto exchange hacked, millions stolen, price crashes")
    assert label == "bearish", f"Expected bearish, got {label} (compound={compound})"
    assert compound < 0


def test_vader_classifies_neutral_headline():
    label, compound = classify_sentiment("Ethereum developers hold weekly call")
    assert label == "neutral", f"Expected neutral, got {label} (compound={compound})"
    assert abs(compound) < VADER_THRESHOLDS["strong_bullish"]


def test_vader_compound_is_float_in_range():
    _, compound = classify_sentiment("Bitcoin price update")
    assert isinstance(compound, float)
    assert -1.0 <= compound <= 1.0


def test_vader_returns_tuple():
    result = classify_sentiment("Market update")
    assert isinstance(result, tuple) and len(result) == 2


# ── build_db_item includes vader_compound ────────────────────

def test_build_db_item_has_vader_compound():
    item = build_db_item(
        title="Solana breaks out to new highs",
        published_at=datetime.utcnow(),
        source="coindesk.com",
        symbol="SOLUSDT",
    )
    assert "vader_compound" in item
    assert isinstance(item["vader_compound"], float)


def test_build_db_item_sentiment_matches_compound():
    item = build_db_item(
        title="Market crashes, investors panic sell",
        published_at=datetime.utcnow(),
        source="cointelegraph.com",
        symbol="BTCUSDT",
    )
    if item["sentiment"] == "bearish":
        assert item["vader_compound"] < 0
    elif item["sentiment"] == "bullish":
        assert item["vader_compound"] > 0


# ── calc_news_modifier uses VADER compound ────────────────────

def test_news_modifier_uses_vader_compound_bullish():
    mock_db = MagicMock()
    mock_db.get_recent_news.return_value = [
        {"sentiment": "bullish", "vader_compound": 0.6, "is_critical": False},
        {"sentiment": "bullish", "vader_compound": 0.5, "is_critical": False},
        {"sentiment": "bullish", "vader_compound": 0.4, "is_critical": False},
        {"sentiment": "neutral", "vader_compound": 0.1, "is_critical": False},
        {"sentiment": "neutral", "vader_compound": 0.0, "is_critical": False},
    ]
    modifier = calc_news_modifier("BTCUSDT", mock_db)
    assert modifier > 0, f"Expected positive modifier for bullish VADER, got {modifier}"


def test_news_modifier_uses_vader_compound_bearish():
    mock_db = MagicMock()
    mock_db.get_recent_news.return_value = [
        {"sentiment": "bearish", "vader_compound": -0.6, "is_critical": False},
        {"sentiment": "bearish", "vader_compound": -0.7, "is_critical": False},
        {"sentiment": "bearish", "vader_compound": -0.5, "is_critical": False},
        {"sentiment": "bearish", "vader_compound": -0.4, "is_critical": False},
        {"sentiment": "neutral", "vader_compound": -0.1, "is_critical": False},
    ]
    modifier = calc_news_modifier("BTCUSDT", mock_db)
    assert modifier < 0, f"Expected negative modifier for bearish VADER, got {modifier}"


def test_news_modifier_empty_news_returns_zero():
    mock_db = MagicMock()
    mock_db.get_recent_news.return_value = []
    assert calc_news_modifier("SOLUSDT", mock_db) == 0.0


def test_news_modifier_scales_with_article_count():
    """Fewer articles → lower confidence → smaller modifier."""
    mock_db = MagicMock()
    # 1 article
    mock_db.get_recent_news.return_value = [
        {"sentiment": "bullish", "vader_compound": 0.8, "is_critical": False},
    ]
    mod_1 = calc_news_modifier("SOLUSDT", mock_db)
    # 5 articles
    mock_db.get_recent_news.return_value = [
        {"sentiment": "bullish", "vader_compound": 0.8, "is_critical": False},
    ] * 5
    mod_5 = calc_news_modifier("SOLUSDT", mock_db)
    assert mod_5 >= mod_1, "More articles should give >= confidence"


# ── RSS feeds config ──────────────────────────────────────────

def test_rss_feeds_count_is_14():
    """Should have original 4 + 10 new = 14 total."""
    assert len(RSS_FEEDS) == 14, f"Expected 14 RSS feeds, got {len(RSS_FEEDS)}"


def test_rss_feeds_all_start_with_https():
    for url in RSS_FEEDS:
        assert url.startswith("https://"), f"Non-HTTPS feed: {url}"


def test_vader_thresholds_in_config():
    assert "strong_bullish" in VADER_THRESHOLDS
    assert "strong_bearish" in VADER_THRESHOLDS
    assert VADER_THRESHOLDS["strong_bullish"] > 0
    assert VADER_THRESHOLDS["strong_bearish"] < 0
