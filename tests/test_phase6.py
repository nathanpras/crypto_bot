import pytest
import sys
from pathlib import Path
from unittest.mock import patch
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import REGIME_WEIGHTS, SIGNAL_WEIGHTS, KILL_ZONE_BONUS


def test_regime_weights_all_sum_to_one():
    for regime, weights in REGIME_WEIGHTS.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.001, f"{regime} weights sum = {total}"


def test_get_regime_weights_trending_bull():
    from signals.engine import get_regime_weights
    w = get_regime_weights("TRENDING_BULL")
    assert w["trend_alignment"] > SIGNAL_WEIGHTS["trend_alignment"]


def test_get_regime_weights_ranging():
    from signals.engine import get_regime_weights
    w_ranging  = get_regime_weights("RANGING")
    w_trending = get_regime_weights("TRENDING_BULL")
    assert w_ranging["rsi_momentum"] > w_trending["rsi_momentum"]


def test_get_regime_weights_fallback():
    from signals.engine import get_regime_weights
    w = get_regime_weights("UNKNOWN_REGIME")
    assert w == SIGNAL_WEIGHTS


def test_kill_zone_modifier_inside():
    from signals.engine import get_kill_zone_modifier
    fake_time = datetime(2026, 6, 2, 8, 0, 0)
    with patch("signals.engine.datetime") as mock_dt:
        mock_dt.utcnow.return_value = fake_time
        in_zone, modifier = get_kill_zone_modifier()
    assert in_zone is True
    assert modifier == KILL_ZONE_BONUS


def test_kill_zone_modifier_outside():
    from signals.engine import get_kill_zone_modifier
    fake_time = datetime(2026, 6, 2, 5, 0, 0)
    with patch("signals.engine.datetime") as mock_dt:
        mock_dt.utcnow.return_value = fake_time
        in_zone, modifier = get_kill_zone_modifier()
    assert in_zone is False
    assert modifier == 0
