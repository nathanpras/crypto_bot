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


# ── Task 2 tests ──────────────────────────────────────────────

from trade_journal.journal import (
    calc_tp_prices,
    calc_pnl,
    generate_weekly_report,
    detect_performance_gap,
)


def test_calc_tp_prices_basic():
    tp1, tp2 = calc_tp_prices(entry=142.34, stop=128.10)
    risk_pct = (142.34 - 128.10) / 142.34
    assert abs(tp1 - 142.34 * (1 + risk_pct * 2.5)) < 0.01
    assert abs(tp2 - 142.34 * (1 + risk_pct * 5.0)) < 0.01
    assert tp1 < tp2


def test_close_trade_tp1_calculates_pnl():
    tp1, _ = calc_tp_prices(entry=142.34, stop=128.10)
    result  = calc_pnl(
        symbol="SOLUSDT",
        entry_price=142.34,
        exit_price=tp1,
        stop_price=128.10,
        portfolio_usd=561.0,
    )
    assert result["pnl_usd"] > 0
    assert 2.3 < result["r_multiple"] < 2.7
    assert result["pnl_idr"] > 0


def test_generate_weekly_report_empty():
    report = generate_weekly_report(pd.DataFrame(), "2026-05-25", "2026-06-01")
    assert "Tidak ada trade" in report


def test_performance_gap_detector_triggers(db):
    db.conn.execute("""
        INSERT INTO backtest_results
            (run_id, run_date, weights_json, train_start, train_end,
             val_start, val_end, train_win_rate, val_win_rate,
             train_sharpe, val_sharpe, total_trades, avg_r, max_drawdown, deployed)
        VALUES ('TEST-01', now(), '{}', '2023-01-01', '2024-06-30',
                '2024-07-01', '2024-12-31', 65.0, 68.0,
                1.5, 1.2, 50, 2.3, -0.12, TRUE)
    """)
    live_trades = pd.DataFrame({
        "pnl_usd":    [-5, -3, 8, -4, -2, -6, -3, 10, -1, -4],
        "r_multiple": [-1, -1, 2, -1, -1, -1, -1, 2, -1, -1],
    })
    gap = detect_performance_gap(live_trades, db)
    assert gap is not None
    assert gap["wr_gap"] >= 0.20


def test_performance_gap_below_min_sample(db):
    live_trades = pd.DataFrame({
        "pnl_usd":    [-5, -3, 8],
        "r_multiple": [-1, -1, 2],
    })
    gap = detect_performance_gap(live_trades, db)
    assert gap is None
