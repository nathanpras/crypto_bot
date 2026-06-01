# tests/test_token_unlocks.py
import pytest
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.token_unlocks import (
    calc_unlock_penalty,
    get_unlock_penalty,
)


def test_penalty_unlock_in_7_days():
    """Unlock dalam 7 hari = penalty 25 (+ 0 jika small)."""
    today = date.today()
    unlocks = [{
        "unlock_date":       today + timedelta(days=5),
        "unlock_amount_usd": 10_000_000,
        "unlock_pct_supply": 2.0,
        "category":          "investor",
    }]
    penalty = calc_unlock_penalty(unlocks)
    assert penalty == 25


def test_penalty_unlock_in_14_days():
    """Unlock dalam 8-14 hari = penalty 20."""
    today = date.today()
    unlocks = [{
        "unlock_date":       today + timedelta(days=12),
        "unlock_amount_usd": 50_000_000,
        "unlock_pct_supply": 3.0,
        "category":          "team",
    }]
    penalty = calc_unlock_penalty(unlocks)
    assert penalty == 20


def test_penalty_unlock_large_supply_adds_extra():
    """Unlock > 5% supply = +10 tambahan."""
    today = date.today()
    unlocks = [{
        "unlock_date":       today + timedelta(days=10),
        "unlock_amount_usd": 100_000_000,
        "unlock_pct_supply": 7.5,   # > 5% = large
        "category":          "investor",
    }]
    penalty = calc_unlock_penalty(unlocks)
    assert penalty == 30   # 20 (14 days) + 10 (large supply)


def test_penalty_unlock_in_30_days():
    """Unlock dalam 15-30 hari = penalty 10."""
    today = date.today()
    unlocks = [{
        "unlock_date":       today + timedelta(days=25),
        "unlock_amount_usd": 20_000_000,
        "unlock_pct_supply": 2.0,
        "category":          "ecosystem",
    }]
    penalty = calc_unlock_penalty(unlocks)
    assert penalty == 10


def test_penalty_no_unlocks():
    """Tidak ada unlock = penalty 0."""
    penalty = calc_unlock_penalty([])
    assert penalty == 0


def test_penalty_uses_worst_upcoming_unlock():
    """Multiple unlocks: ambil yang terburuk (closest)."""
    today = date.today()
    unlocks = [
        {"unlock_date": today + timedelta(days=25), "unlock_pct_supply": 2.0,
         "unlock_amount_usd": 10_000_000, "category": "ecosystem"},
        {"unlock_date": today + timedelta(days=6),  "unlock_pct_supply": 2.0,
         "unlock_amount_usd": 10_000_000, "category": "investor"},
    ]
    penalty = calc_unlock_penalty(unlocks)
    assert penalty == 25   # 6 hari = days_7 bucket


def test_get_unlock_penalty_returns_zero_when_no_data():
    """Jika DB kosong, penalty = 0."""
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mock_db.get_upcoming_unlocks.return_value = []

    penalty = get_unlock_penalty("BTCUSDT", mock_db)
    assert penalty == 0
