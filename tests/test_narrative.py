# tests/test_narrative.py
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.narrative import (
    calc_sector_modifier,
    get_sector_modifier,
)


def test_sector_modifier_strong_up():
    """TVL +25% dalam 30 hari = +5 modifier."""
    mod, label = calc_sector_modifier(tvl_change_30d=25.0)
    assert mod == 5
    assert "strong_up" in label.lower() or "+" in label


def test_sector_modifier_mild_up():
    """TVL +12% dalam 30 hari = +2 modifier."""
    mod, label = calc_sector_modifier(tvl_change_30d=12.0)
    assert mod == 2


def test_sector_modifier_neutral():
    """TVL +5% dalam 30 hari = 0 modifier."""
    mod, label = calc_sector_modifier(tvl_change_30d=5.0)
    assert mod == 0


def test_sector_modifier_mild_down():
    """TVL -15% dalam 30 hari = -3 modifier."""
    mod, label = calc_sector_modifier(tvl_change_30d=-15.0)
    assert mod == -3


def test_sector_modifier_strong_down():
    """TVL -25% dalam 30 hari = -8 modifier."""
    mod, label = calc_sector_modifier(tvl_change_30d=-25.0)
    assert mod == -8


def test_sector_modifier_exactly_on_threshold():
    """TVL tepat +20% = strong_up threshold."""
    mod, _ = calc_sector_modifier(tvl_change_30d=20.0)
    assert mod == 5


def test_get_sector_modifier_returns_zero_when_no_data():
    """Jika DB tidak ada data TVL, return 0 (jangan block trade)."""
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mock_db.get_sector_tvl.return_value = {}   # kosong

    mod = get_sector_modifier("SOLUSDT", mock_db)
    assert mod == 0


def test_get_sector_modifier_uses_sector_map():
    """ARBUSDT harus lookup sektor 'arbitrum'."""
    from unittest.mock import MagicMock, call
    mock_db = MagicMock()
    mock_db.get_sector_tvl.return_value = {"tvl_change_30d": 18.5}

    mod = get_sector_modifier("ARBUSDT", mock_db)
    mock_db.get_sector_tvl.assert_called_once_with("arbitrum")
    assert mod == 2   # 18.5% = mild_up
