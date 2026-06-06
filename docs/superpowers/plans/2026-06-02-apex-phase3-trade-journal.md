# APEX Phase 3 — Trade Journal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tambah trade journal via Telegram bot sehingga user bisa log entry/exit manual, terima reminder otomatis saat TP/SL tercapai, dan lacak performa real vs backtest untuk feedback loop adaptif.

**Architecture:** Bot polling berjalan sebagai asyncio coroutine di dalam `--run` mode yang sudah ada. Modul baru `trade_journal/` berisi logika journal, reminder, dan bot handler. Data disimpan di tabel `journal_trades` baru di DuckDB yang sudah ada.

**Tech Stack:** Python 3.11, DuckDB, asyncio, requests (Telegram REST), pandas, loguru

---

## File Structure

```
trade_journal/
  __init__.py         [CREATE] empty
  journal.py          [CREATE] pure functions: P&L calc, report generator, gap detector
  reminder.py         [CREATE] TP/SL price monitor, hook ke WebSocket
  bot.py              [CREATE] Telegram polling + command handlers

tests/
  test_trade_journal.py  [CREATE] 8 unit tests

database.py           [MODIFY] tambah SCHEMA_PHASE3 + 10 metode baru
utils/telegram.py     [MODIFY] tambah get_updates()
collector/realtime.py [MODIFY] panggil check_tp_sl_reminders di handle_message
main.py               [MODIFY] tambah poll_telegram_commands + run_weekly_report_scheduler ke live_loop
```

---

## Task 1: Database Schema + Methods

**Files:**
- Modify: `database.py`
- Test: `tests/test_trade_journal.py` (subset)

- [ ] **Step 1: Tulis failing tests untuk DB methods**

Buat `tests/test_trade_journal.py`:

```python
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


# ── Task 1 tests ──────────────────────────────────────────────

def test_open_trade_creates_record(db):
    """open_journal_trade harus simpan record dengan status='open'."""
    trade_id = db.open_journal_trade(
        symbol="SOLUSDT",
        entry_price=142.34,
        stop_price=128.10,
        tp1_price=177.93,
        tp2_price=213.51,
        signal_score=87.0,
        signal_id="SOL-0602",
    )
    assert trade_id is not None
    trade = db.get_open_journal_trade_by_symbol("SOLUSDT")
    assert trade is not None
    assert trade["symbol"] == "SOLUSDT"
    assert trade["entry_price"] == 142.34
    assert trade["status"] == "open"


def test_auto_link_signal_within_48h(db):
    """get_last_signal_for_symbol harus return sinyal dalam 48 jam."""
    # Insert a recent fired signal
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
    """get_last_signal_for_symbol harus return None jika tidak ada sinyal recent."""
    sig = db.get_last_signal_for_symbol("ETHUSDT", within_hours=48)
    assert sig is None
```

- [ ] **Step 2: Jalankan — pastikan FAIL**

```bash
py -m pytest tests/test_trade_journal.py -v 2>&1 | head -20
```

Expected: `AttributeError: 'Database' object has no attribute 'open_journal_trade'`

- [ ] **Step 3: Tambah SCHEMA_PHASE3 ke database.py**

Buka `database.py`, tambahkan string ini setelah `SCHEMA_PHASE2`:

```python
SCHEMA_PHASE3 = """
CREATE TABLE IF NOT EXISTS journal_trades (
    id                  VARCHAR PRIMARY KEY,
    symbol              VARCHAR NOT NULL,
    entry_price         DOUBLE NOT NULL,
    stop_price          DOUBLE NOT NULL,
    tp1_price           DOUBLE NOT NULL,
    tp2_price           DOUBLE NOT NULL,
    open_time           TIMESTAMP NOT NULL,
    close_time          TIMESTAMP,
    exit_price          DOUBLE,
    exit_reason         VARCHAR,
    pnl_usd             DOUBLE,
    pnl_idr             DOUBLE,
    r_multiple          DOUBLE,
    signal_score        DOUBLE,
    signal_id           VARCHAR,
    status              VARCHAR DEFAULT 'open',
    notes               VARCHAR,
    reminder_sent_at    TIMESTAMP
);
"""
```

- [ ] **Step 4: Update _init_schema() untuk eksekusi SCHEMA_PHASE3**

Cari method `_init_schema` di `database.py` dan tambahkan satu baris:

```python
def _init_schema(self):
    self.conn.execute(SCHEMA)
    self.conn.execute(SCHEMA_PHASE2)
    self.conn.execute(SCHEMA_PHASE3)    # tambah baris ini
```

- [ ] **Step 5: Tambah 10 metode baru ke class Database**

Tambahkan metode-metode ini di akhir class `Database` (setelah metode terakhir yang ada):

```python
# ── Trade Journal ─────────────────────────────────────────────

def open_journal_trade(self, symbol: str, entry_price: float,
                       stop_price: float, tp1_price: float,
                       tp2_price: float, signal_score: float | None,
                       signal_id: str | None) -> str:
    """Simpan trade baru ke journal. Return trade_id."""
    import uuid
    trade_id  = str(uuid.uuid4())[:12]
    open_time = datetime.utcnow()
    self.conn.execute("""
        INSERT INTO journal_trades
            (id, symbol, entry_price, stop_price, tp1_price, tp2_price,
             open_time, signal_score, signal_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
    """, [trade_id, symbol, entry_price, stop_price,
          tp1_price, tp2_price, open_time, signal_score, signal_id])
    return trade_id


def close_journal_trade(self, trade_id: str, exit_price: float,
                        exit_reason: str, pnl_usd: float,
                        pnl_idr: float, r_multiple: float):
    """Tutup trade — set status closed dan isi outcome fields."""
    self.conn.execute("""
        UPDATE journal_trades
        SET status='closed', close_time=?, exit_price=?,
            exit_reason=?, pnl_usd=?, pnl_idr=?, r_multiple=?
        WHERE id=?
    """, [datetime.utcnow(), exit_price, exit_reason,
          pnl_usd, pnl_idr, r_multiple, trade_id])


def get_open_journal_trades(self) -> list[dict]:
    """Return semua posisi terbuka sebagai list of dict."""
    rows = self.conn.execute("""
        SELECT id, symbol, entry_price, stop_price, tp1_price, tp2_price,
               open_time, signal_score, signal_id, reminder_sent_at
        FROM journal_trades WHERE status = 'open'
        ORDER BY open_time
    """).fetchall()
    cols = ["id", "symbol", "entry_price", "stop_price", "tp1_price",
            "tp2_price", "open_time", "signal_score", "signal_id",
            "reminder_sent_at"]
    return [dict(zip(cols, row)) for row in rows]


def get_open_journal_trades_by_symbol(self, symbol: str) -> list[dict]:
    """Return posisi terbuka untuk satu symbol."""
    rows = self.conn.execute("""
        SELECT id, symbol, entry_price, stop_price, tp1_price, tp2_price,
               open_time, signal_score, signal_id, reminder_sent_at
        FROM journal_trades WHERE status = 'open' AND symbol = ?
        ORDER BY open_time
    """, [symbol]).fetchall()
    cols = ["id", "symbol", "entry_price", "stop_price", "tp1_price",
            "tp2_price", "open_time", "signal_score", "signal_id",
            "reminder_sent_at"]
    return [dict(zip(cols, row)) for row in rows]


def get_open_journal_trade_by_symbol(self, symbol: str) -> dict | None:
    """Return satu posisi terbuka paling baru untuk symbol, atau None."""
    trades = self.get_open_journal_trades_by_symbol(symbol)
    return trades[-1] if trades else None


def get_journal_trades_by_period(self, date_from: str,
                                  date_to: str) -> pd.DataFrame:
    """Return semua closed trades dalam periode sebagai DataFrame."""
    return self.conn.execute("""
        SELECT symbol, entry_price, exit_price, exit_reason,
               pnl_usd, pnl_idr, r_multiple, signal_score,
               open_time, close_time
        FROM journal_trades
        WHERE status = 'closed'
          AND close_time >= ? AND close_time <= ?
        ORDER BY close_time
    """, [date_from, date_to + " 23:59:59"]).df()


def get_last_signal_for_symbol(self, symbol: str,
                                within_hours: int = 48) -> dict | None:
    """Return sinyal APEX terbaru yang fire untuk symbol dalam N jam."""
    cutoff = datetime.utcnow() - timedelta(hours=within_hours)
    result = self.conn.execute("""
        SELECT timestamp, total_score
        FROM signals
        WHERE symbol = ? AND timestamp >= ? AND fire = TRUE
        ORDER BY timestamp DESC LIMIT 1
    """, [symbol, cutoff]).fetchone()
    if result:
        ts = result[0]
        if hasattr(ts, 'strftime'):
            sid = f"{symbol[:3]}-{ts.strftime('%m%d')}"
        else:
            sid = f"{symbol[:3]}-manual"
        return {"total_score": result[1], "signal_id": sid}
    return None


def update_journal_reminder_sent(self, trade_id: str):
    """Catat waktu terakhir reminder dikirim (anti-spam)."""
    self.conn.execute("""
        UPDATE journal_trades SET reminder_sent_at = ? WHERE id = ?
    """, [datetime.utcnow(), trade_id])


def get_last_deployed_backtest(self) -> dict | None:
    """Return hasil backtest terakhir yang di-deploy."""
    result = self.conn.execute("""
        SELECT val_win_rate, avg_r, val_sharpe
        FROM backtest_results
        WHERE deployed = TRUE
        ORDER BY run_date DESC LIMIT 1
    """).fetchone()
    if result:
        return {"val_win_rate": result[0], "avg_r": result[1],
                "val_sharpe": result[2]}
    return None


def get_latest_price(self, symbol: str) -> float | None:
    """Return harga close terbaru untuk symbol dari candles 4H."""
    result = self.conn.execute("""
        SELECT close FROM candles
        WHERE symbol = ? AND timeframe = '4h'
        ORDER BY timestamp DESC LIMIT 1
    """, [symbol]).fetchone()
    return result[0] if result else None
```

- [ ] **Step 6: Jalankan tests Task 1 — pastikan PASS**

```bash
py -m pytest tests/test_trade_journal.py::test_open_trade_creates_record tests/test_trade_journal.py::test_auto_link_signal_within_48h tests/test_trade_journal.py::test_auto_link_returns_null_if_no_signal -v
```

Expected: `3 passed`

- [ ] **Step 7: Commit**

```bash
cd "c:\Users\jonat\Downloads\CryptoAgent\CryptoAgent"
git add database.py tests/test_trade_journal.py
git commit -m "feat: add journal_trades schema + DB methods for Phase 3"
```

---

## Task 2: Journal Core — P&L Calculator + Report Generator

**Files:**
- Create: `trade_journal/__init__.py`
- Create: `trade_journal/journal.py`
- Test: `tests/test_trade_journal.py` (tambah tests)

- [ ] **Step 1: Tambah failing tests untuk journal.py**

Append ke `tests/test_trade_journal.py`:

```python
# ── Task 2 tests ──────────────────────────────────────────────

from trade_journal.journal import (
    calc_tp_prices,
    calc_pnl,
    generate_weekly_report,
    detect_performance_gap,
)


def test_calc_tp_prices_basic():
    """TP1 = 2.5R, TP2 = 5.0R dari entry dan stop."""
    tp1, tp2 = calc_tp_prices(entry=142.34, stop=128.10)
    risk_pct = (142.34 - 128.10) / 142.34   # ~0.1001
    assert abs(tp1 - 142.34 * (1 + risk_pct * 2.5)) < 0.01
    assert abs(tp2 - 142.34 * (1 + risk_pct * 5.0)) < 0.01
    assert tp1 < tp2


def test_close_trade_tp1_calculates_pnl():
    """P&L di TP1 harus positif, r_multiple ~ 2.5R."""
    tp1, _ = calc_tp_prices(entry=142.34, stop=128.10)
    result  = calc_pnl(
        symbol="SOLUSDT",
        entry_price=142.34,
        exit_price=tp1,
        stop_price=128.10,
        portfolio_usd=561.0,
    )
    assert result["pnl_usd"] > 0
    assert 2.3 < result["r_multiple"] < 2.7   # ~2.5R
    assert result["pnl_idr"] > 0


def test_generate_weekly_report_empty():
    """Report dengan trades kosong tidak crash."""
    report = generate_weekly_report(pd.DataFrame(), "2026-05-25", "2026-06-01")
    assert "Tidak ada trade" in report


def test_performance_gap_detector_triggers(db):
    """detect_performance_gap harus return dict jika gap > threshold."""
    # Simpan deployed backtest dengan win rate 68%
    db.conn.execute("""
        INSERT INTO backtest_results
            (run_id, run_date, weights_json, train_start, train_end,
             val_start, val_end, train_win_rate, val_win_rate,
             train_sharpe, val_sharpe, total_trades, avg_r, max_drawdown, deployed)
        VALUES ('TEST-01', now(), '{}', '2023-01-01', '2024-06-30',
                '2024-07-01', '2024-12-31', 65.0, 68.0,
                1.5, 1.2, 50, 2.3, -0.12, TRUE)
    """)
    # Buat live trades dengan win rate 35% (gap > 20pp)
    live_trades = pd.DataFrame({
        "pnl_usd":    [-5, -3, 8, -4, -2, -6, -3, 10, -1, -4],
        "r_multiple": [-1, -1, 2, -1, -1, -1, -1, 2, -1, -1],
    })
    gap = detect_performance_gap(live_trades, db)
    assert gap is not None
    assert gap["wr_gap"] >= 0.20


def test_performance_gap_below_min_sample(db):
    """Tidak ada alert jika trade < 5."""
    live_trades = pd.DataFrame({
        "pnl_usd":    [-5, -3, 8],
        "r_multiple": [-1, -1, 2],
    })
    gap = detect_performance_gap(live_trades, db)
    assert gap is None
```

- [ ] **Step 2: Jalankan — pastikan FAIL**

```bash
py -m pytest tests/test_trade_journal.py -k "tp_prices or pnl or report or gap" -v 2>&1 | head -15
```

Expected: `ImportError: cannot import name 'calc_tp_prices'`

- [ ] **Step 3: Buat `trade_journal/__init__.py`**

```python
```

(file kosong)

- [ ] **Step 4: Buat `trade_journal/journal.py`**

```python
# trade_journal/journal.py
import os
from datetime import datetime, timedelta
import pandas as pd
from config import COINS, RISK_PER_TRADE


def calc_tp_prices(entry_price: float, stop_price: float) -> tuple[float, float]:
    """Return (tp1, tp2) — 2.5R dan 5.0R dari entry/stop."""
    risk_pct = (entry_price - stop_price) / entry_price
    tp1 = round(entry_price * (1 + risk_pct * 2.5), 4)
    tp2 = round(entry_price * (1 + risk_pct * 5.0), 4)
    return tp1, tp2


def calc_pnl(symbol: str, entry_price: float, exit_price: float,
             stop_price: float, portfolio_usd: float) -> dict:
    """Hitung P&L dari trade yang ditutup."""
    pnl_pct    = (exit_price - entry_price) / entry_price
    risk_pct   = (entry_price - stop_price) / entry_price
    r_multiple = round(pnl_pct / risk_pct, 2) if risk_pct > 0 else 0.0
    tier       = COINS.get(symbol, {}).get("tier", 3)
    risk_usd   = portfolio_usd * RISK_PER_TRADE.get(tier, 0.01)
    pnl_usd    = round(risk_usd * r_multiple, 2)
    idr_rate   = float(os.getenv("IDR_RATE", "17800"))
    pnl_idr    = round(pnl_usd * idr_rate, 0)
    return {
        "pnl_pct":    round(pnl_pct, 4),
        "pnl_usd":    pnl_usd,
        "pnl_idr":    pnl_idr,
        "r_multiple": r_multiple,
    }


def format_open_msg(symbol: str, entry: float, stop: float,
                    tp1: float, tp2: float,
                    signal_score: float | None,
                    signal_linked: bool) -> str:
    risk_pct = (entry - stop) / entry * 100
    msg = (
        f"✅ <b>Trade dibuka</b>\n"
        f"{symbol} | Entry ${entry:.4f} | Stop ${stop:.4f}\n"
        f"TP1: ${tp1:.4f} (+{(tp1/entry-1)*100:.1f}%) | "
        f"TP2: ${tp2:.4f} (+{(tp2/entry-1)*100:.1f}%)\n"
        f"Risk: {risk_pct:.1f}%"
    )
    if signal_linked and signal_score is not None:
        msg += f"\n🔗 Linked ke sinyal score {signal_score:.0f}"
    else:
        msg += "\n⚠️ Tidak ada sinyal APEX dalam 48 jam — dicatat tanpa link"
    return msg


def format_close_msg(symbol: str, exit_price: float, exit_reason: str,
                     pnl_usd: float, pnl_idr: float,
                     r_multiple: float, hold_hours: float) -> str:
    icon     = "✅" if pnl_usd >= 0 else "❌"
    pnl_sign = "+" if pnl_usd >= 0 else ""
    return (
        f"{icon} <b>Trade ditutup</b>\n"
        f"{symbol} | Exit ${exit_price:.4f} ({exit_reason})\n"
        f"P&L: {pnl_sign}${pnl_usd:.2f} (Rp {pnl_idr:+,.0f})\n"
        f"R-Multiple: {r_multiple:+.2f}R | Hold: {hold_hours:.0f} jam"
    )


def generate_weekly_report(trades_df: pd.DataFrame,
                            date_from: str, date_to: str) -> str:
    if trades_df.empty:
        return (
            f"📊 <b>APEX Weekly Report</b>\n"
            f"{date_from} – {date_to}\n\n"
            f"Tidak ada trade minggu ini."
        )

    total    = len(trades_df)
    wins     = int((trades_df["pnl_usd"] > 0).sum())
    losses   = total - wins
    win_rate = wins / total
    pnl_sum  = trades_df["pnl_usd"].sum()
    idr_sum  = trades_df["pnl_idr"].sum() if "pnl_idr" in trades_df else pnl_sum * 17800
    avg_r    = trades_df["r_multiple"].mean()

    best_idx  = trades_df["pnl_usd"].idxmax()
    worst_idx = trades_df["pnl_usd"].idxmin()
    best      = trades_df.loc[best_idx]
    worst     = trades_df.loc[worst_idx]

    by_coin = trades_df.groupby("symbol").agg(
        count=("pnl_usd", "count"),
        wins=("pnl_usd", lambda x: int((x > 0).sum())),
        pnl=("pnl_usd", "sum"),
    )
    coin_lines = "\n".join(
        f"{sym:<10} {int(row['count'])} trade | "
        f"{int(row['wins'])}W {int(row['count']-row['wins'])}L | "
        f"${row['pnl']:+.2f}"
        for sym, row in by_coin.iterrows()
    )

    sig_section = ""
    if "signal_score" in trades_df.columns:
        high   = trades_df[trades_df["signal_score"] >= 80]
        medium = trades_df[
            (trades_df["signal_score"] >= 70) &
            (trades_df["signal_score"] <  80)
        ]
        high_wr   = (high["pnl_usd"] > 0).mean() * 100   if len(high)   > 0 else 0
        medium_wr = (medium["pnl_usd"] > 0).mean() * 100 if len(medium) > 0 else 0
        sig_section = (
            f"\n── Signal Accuracy ───────────────────\n"
            f"Score ≥80   → {len(high)} trade | {high_wr:.0f}% WR "
            f"{'✅' if high_wr >= 60 else '⚠️'}\n"
            f"Score 70-79 → {len(medium)} trade | {medium_wr:.0f}% WR "
            f"{'✅' if medium_wr >= 60 else '⚠️'}"
        )

    sign = "+" if pnl_sum >= 0 else ""
    best_score  = f"{best.get('signal_score', '—'):.0f}" if pd.notna(best.get("signal_score")) else "—"
    worst_score = f"{worst.get('signal_score', '—'):.0f}" if pd.notna(worst.get("signal_score")) else "—"

    return (
        f"📊 <b>APEX Weekly Report</b> — {date_from} – {date_to}\n"
        f"{'═'*39}\n"
        f"Trades  : {total}  |  Win: {wins}  |  Loss: {losses}\n"
        f"Win rate: {win_rate*100:.0f}%  |  Avg R: {avg_r:.1f}R\n"
        f"P&L     : {sign}${pnl_sum:.2f}  (Rp {idr_sum:+,.0f})\n"
        f"\n── Per Coin ──────────────────────────\n"
        f"{coin_lines}\n"
        f"\n── Best Trade ────────────────────────\n"
        f"{best['symbol']} ${best['pnl_usd']:+.2f} | Score {best_score} | {best['r_multiple']:.1f}R\n"
        f"\n── Worst Trade ───────────────────────\n"
        f"{worst['symbol']} ${worst['pnl_usd']:+.2f} | Score {worst_score} | {worst['r_multiple']:.1f}R"
        f"{sig_section}\n"
        f"{'═'*39}"
    )


GAP_THRESHOLD_WINRATE = 0.20
GAP_THRESHOLD_AVG_R   = 0.50
MIN_SAMPLE_TRADES     = 5


def detect_performance_gap(live_trades: pd.DataFrame, db) -> dict | None:
    """Bandingkan live performance vs ekspektasi backtest terakhir."""
    if len(live_trades) < MIN_SAMPLE_TRADES:
        return None

    last_bt = db.get_last_deployed_backtest()
    if not last_bt:
        return None

    live_wr    = float((live_trades["pnl_usd"] > 0).mean())
    live_avg_r = float(live_trades["r_multiple"].mean())
    bt_wr      = last_bt["val_win_rate"] / 100.0
    bt_avg_r   = last_bt.get("avg_r", 2.0) or 2.0

    wr_gap = bt_wr - live_wr
    r_gap  = bt_avg_r - live_avg_r

    if wr_gap >= GAP_THRESHOLD_WINRATE or r_gap >= GAP_THRESHOLD_AVG_R:
        return {
            "live_wr":    live_wr,
            "live_avg_r": live_avg_r,
            "bt_wr":      bt_wr,
            "bt_avg_r":   bt_avg_r,
            "wr_gap":     wr_gap,
            "r_gap":      r_gap,
        }
    return None


def format_gap_alert(gap: dict) -> str:
    return (
        f"⚠️ <b>APEX Performance Gap Detected</b>\n\n"
        f"Live: {gap['live_wr']*100:.0f}% WR | {gap['live_avg_r']:.1f}R avg\n"
        f"Backtest ekspektasi: {gap['bt_wr']*100:.0f}% WR | {gap['bt_avg_r']:.1f}R avg\n"
        f"Gap: -{gap['wr_gap']*100:.0f}pp win rate | -{gap['r_gap']:.1f}R\n\n"
        f"Saran: <code>python main.py --optimize-weights</code>"
    )
```

- [ ] **Step 5: Jalankan tests Task 2 — pastikan PASS**

```bash
py -m pytest tests/test_trade_journal.py -v 2>&1 | tail -15
```

Expected: `8 passed` (3 dari Task 1 + 5 dari Task 2)

- [ ] **Step 6: Commit**

```bash
git add trade_journal/__init__.py trade_journal/journal.py tests/test_trade_journal.py
git commit -m "feat: add trade_journal module — P&L calculator, report generator, gap detector"
```

---

## Task 3: Reminder Module

**Files:**
- Create: `trade_journal/reminder.py`
- Test: `tests/test_trade_journal.py` (tambah tests)

- [ ] **Step 1: Tambah failing tests untuk reminder**

Append ke `tests/test_trade_journal.py`:

```python
# ── Task 3 tests ──────────────────────────────────────────────

from unittest.mock import MagicMock, patch
from trade_journal.reminder import check_tp_sl_reminders
import asyncio


def test_tp_sl_reminder_fires_on_candle(db):
    """Reminder dikirim saat candle high melewati TP1."""
    db.open_journal_trade(
        symbol="SOLUSDT", entry_price=142.34, stop_price=128.10,
        tp1_price=177.93, tp2_price=213.51,
        signal_score=87.0, signal_id="SOL-0602",
    )
    sent = []
    candle = {"symbol": "SOLUSDT", "high": 180.0, "low": 141.0}
    asyncio.run(check_tp_sl_reminders(candle, db, sent.append))
    assert len(sent) == 1
    assert "TP1" in sent[0]
    assert "SOLUSDT" in sent[0]


def test_reminder_anti_spam_4h(db):
    """Reminder tidak dikirim ulang dalam 4 jam."""
    trade_id = db.open_journal_trade(
        symbol="BTCUSDT", entry_price=68000, stop_price=62560,
        tp1_price=82600, tp2_price=97200,
        signal_score=80.0, signal_id="BTC-0602",
    )
    # Simulasi reminder sudah dikirim 1 jam lalu
    db.conn.execute("""
        UPDATE journal_trades
        SET reminder_sent_at = ?
        WHERE id = ?
    """, [datetime.utcnow() - timedelta(hours=1), trade_id])

    sent = []
    candle = {"symbol": "BTCUSDT", "high": 85000.0, "low": 67000.0}
    asyncio.run(check_tp_sl_reminders(candle, db, sent.append))
    assert len(sent) == 0   # tidak boleh kirim karena baru 1 jam
```

- [ ] **Step 2: Jalankan — pastikan FAIL**

```bash
py -m pytest tests/test_trade_journal.py -k "reminder" -v 2>&1 | head -15
```

Expected: `ImportError: cannot import name 'check_tp_sl_reminders'`

- [ ] **Step 3: Buat `trade_journal/reminder.py`**

```python
# trade_journal/reminder.py
from datetime import datetime, timedelta
import pandas as pd
from loguru import logger


async def check_tp_sl_reminders(candle: dict, db, send_fn) -> None:
    """
    Dipanggil setiap candle 4H close.
    Cek semua open trades untuk symbol ini dan kirim reminder jika perlu.
    Anti-spam: 4 jam cooldown per trade.
    """
    symbol = candle.get("symbol", "")
    high   = candle.get("high", 0.0)
    low    = candle.get("low",  float("inf"))

    trades = db.get_open_journal_trades_by_symbol(symbol)
    if not trades:
        return

    now = datetime.utcnow()

    for trade in trades:
        trade_id = trade["id"]

        # Anti-spam: skip jika sudah kirim reminder dalam 4 jam terakhir
        if trade.get("reminder_sent_at"):
            last = trade["reminder_sent_at"]
            if hasattr(last, "to_pydatetime"):
                last = last.to_pydatetime()
            if last.tzinfo:
                last = last.replace(tzinfo=None)
            if (now - last).total_seconds() < 4 * 3600:
                continue

        reminded = False

        if high >= trade["tp1_price"]:
            send_fn(
                f"🎯 <b>{symbol} nyentuh TP1</b> "
                f"(${trade['tp1_price']:.4f})\n"
                f"Sudah close?\n"
                f"<code>/close {symbol} tp1</code>"
            )
            reminded = True

        elif low <= trade["stop_price"]:
            send_fn(
                f"🛑 <b>{symbol} kena stop</b> "
                f"(${trade['stop_price']:.4f})\n"
                f"Sudah close?\n"
                f"<code>/close {symbol} stop</code>"
            )
            reminded = True

        else:
            open_time = trade["open_time"]
            if hasattr(open_time, "to_pydatetime"):
                open_time = open_time.to_pydatetime()
            if open_time.tzinfo:
                open_time = open_time.replace(tzinfo=None)
            days_open = (now - open_time).days
            if days_open >= 7:
                send_fn(
                    f"⏰ <b>{symbol} sudah {days_open} hari terbuka</b>\n"
                    f"Mau di-close?\n"
                    f"<code>/close {symbol} [harga]</code>"
                )
                reminded = True

        if reminded:
            db.update_journal_reminder_sent(trade_id)
            logger.debug(f"Reminder sent for {symbol} trade {trade_id}")
```

- [ ] **Step 4: Jalankan tests Task 3 — pastikan PASS**

```bash
py -m pytest tests/test_trade_journal.py -v 2>&1 | tail -10
```

Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add trade_journal/reminder.py tests/test_trade_journal.py
git commit -m "feat: add TP/SL reminder module with 4h anti-spam"
```

---

## Task 4: Telegram Utils Extension

**Files:**
- Modify: `utils/telegram.py`

- [ ] **Step 1: Tambah `get_updates()` ke utils/telegram.py**

Append setelah fungsi `send_error()` di `utils/telegram.py`:

```python
def get_updates(offset: int = 0) -> list:
    """
    Poll Telegram getUpdates untuk command baru.
    Dipanggil dari asyncio via run_in_executor (blocking call).
    """
    if not TOKEN or TOKEN == "your_bot_token_here":
        return []
    try:
        r = requests.get(
            f"{BASE}/getUpdates",
            params={
                "offset":           offset,
                "timeout":          25,
                "allowed_updates":  ["message"],
            },
            timeout=30,
        )
        data = r.json()
        if data.get("ok"):
            return data.get("result", [])
    except Exception as e:
        logger.error(f"getUpdates failed: {e}")
    return []
```

- [ ] **Step 2: Verifikasi import berjalan**

```bash
py -c "from utils.telegram import get_updates; print('get_updates OK')"
```

Expected: `get_updates OK`

- [ ] **Step 3: Commit**

```bash
git add utils/telegram.py
git commit -m "feat: add get_updates() to telegram utils for bot polling"
```

---

## Task 5: Bot Command Handlers

**Files:**
- Create: `trade_journal/bot.py`

- [ ] **Step 1: Buat `trade_journal/bot.py`**

```python
# trade_journal/bot.py
import asyncio
import os
from datetime import datetime
import pandas as pd
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

ALLOWED_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", "0"))


async def poll_telegram_commands(db) -> None:
    """
    Long-poll Telegram dan dispatch command ke handler.
    Berjalan selamanya sebagai asyncio coroutine.
    """
    from utils.telegram import get_updates, send

    offset = 0
    loop   = asyncio.get_event_loop()

    while True:
        try:
            updates = await loop.run_in_executor(
                None, lambda o=offset: get_updates(o)
            )
            for update in updates:
                offset  = update["update_id"] + 1
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))

                if chat_id != ALLOWED_CHAT_ID:
                    continue

                text = message.get("text", "").strip()
                if text.startswith("/"):
                    try:
                        await handle_command(text, db, send)
                    except Exception as e:
                        logger.error(f"Command error '{text}': {e}")
                        send(f"❌ Error: {e}")

        except Exception as e:
            logger.error(f"Bot polling error: {e}")

        await asyncio.sleep(2)


async def handle_command(text: str, db, send_fn) -> None:
    """Parse teks command dan dispatch ke handler yang sesuai."""
    parts = text.split()
    cmd   = parts[0].lower().split("@")[0]

    if cmd == "/open":
        if len(parts) < 4:
            send_fn("❓ Format: <code>/open SYMBOL ENTRY STOP</code>\n"
                    "Contoh: <code>/open SOLUSDT 142.34 128.10</code>")
            return
        try:
            await cmd_open(parts[1].upper(), float(parts[2]),
                           float(parts[3]), db, send_fn)
        except ValueError:
            send_fn("❓ Entry dan Stop harus angka.")

    elif cmd == "/close":
        if len(parts) < 3:
            send_fn("❓ Format: <code>/close SYMBOL tp1|stop|HARGA</code>\n"
                    "Contoh: <code>/close SOLUSDT tp1</code>")
            return
        await cmd_close(parts[1].upper(), parts[2], db, send_fn)

    elif cmd == "/status":
        await cmd_status(db, send_fn)

    elif cmd == "/report":
        await cmd_report(db, send_fn)

    else:
        send_fn(
            "❓ <b>Command yang tersedia:</b>\n"
            "<code>/open SYMBOL ENTRY STOP</code>\n"
            "<code>/close SYMBOL tp1|stop|HARGA</code>\n"
            "<code>/status</code>\n"
            "<code>/report</code>"
        )


async def cmd_open(symbol: str, entry: float, stop: float,
                   db, send_fn) -> None:
    """Handle /open — catat trade baru."""
    from trade_journal.journal import calc_tp_prices, format_open_msg
    from risk.manager import get_portfolio_usd

    tp1, tp2 = calc_tp_prices(entry, stop)

    sig          = db.get_last_signal_for_symbol(symbol, within_hours=48)
    signal_score = sig["total_score"] if sig else None
    signal_id    = sig["signal_id"]   if sig else None

    trade_id = db.open_journal_trade(
        symbol=symbol, entry_price=entry, stop_price=stop,
        tp1_price=tp1, tp2_price=tp2,
        signal_score=signal_score, signal_id=signal_id,
    )

    msg = format_open_msg(
        symbol=symbol, entry=entry, stop=stop, tp1=tp1, tp2=tp2,
        signal_score=signal_score, signal_linked=signal_id is not None,
    )
    send_fn(msg)
    logger.info(f"Journal: opened {symbol} @ {entry} (id={trade_id})")


async def cmd_close(symbol: str, exit_arg: str, db, send_fn) -> None:
    """Handle /close — catat exit trade."""
    from trade_journal.journal import calc_pnl, format_close_msg
    from risk.manager import get_portfolio_usd

    trade = db.get_open_journal_trade_by_symbol(symbol)
    if not trade:
        send_fn(f"❓ Tidak ada posisi terbuka untuk <b>{symbol}</b>.")
        return

    if exit_arg.lower() == "tp1":
        exit_price, exit_reason = trade["tp1_price"], "TP1"
    elif exit_arg.lower() == "stop":
        exit_price, exit_reason = trade["stop_price"], "Stop"
    else:
        try:
            exit_price, exit_reason = float(exit_arg), "Manual"
        except ValueError:
            send_fn("❓ Gunakan: tp1, stop, atau angka harga.")
            return

    portfolio_usd = get_portfolio_usd()
    pnl = calc_pnl(
        symbol=symbol,
        entry_price=trade["entry_price"],
        exit_price=exit_price,
        stop_price=trade["stop_price"],
        portfolio_usd=portfolio_usd,
    )

    open_dt = trade["open_time"]
    if hasattr(open_dt, "to_pydatetime"):
        open_dt = open_dt.to_pydatetime()
    if open_dt.tzinfo:
        open_dt = open_dt.replace(tzinfo=None)
    hold_hours = (datetime.utcnow() - open_dt).total_seconds() / 3600

    db.close_journal_trade(
        trade_id=trade["id"], exit_price=exit_price,
        exit_reason=exit_reason.lower(),
        pnl_usd=pnl["pnl_usd"], pnl_idr=pnl["pnl_idr"],
        r_multiple=pnl["r_multiple"],
    )

    msg = format_close_msg(
        symbol=symbol, exit_price=exit_price, exit_reason=exit_reason,
        pnl_usd=pnl["pnl_usd"], pnl_idr=pnl["pnl_idr"],
        r_multiple=pnl["r_multiple"], hold_hours=hold_hours,
    )
    send_fn(msg)
    logger.info(f"Journal: closed {symbol} @ {exit_price} | "
                f"P&L: ${pnl['pnl_usd']:.2f}")


async def cmd_status(db, send_fn) -> None:
    """Handle /status — tampilkan posisi terbuka."""
    trades = db.get_open_journal_trades()

    if not trades:
        send_fn("📋 Tidak ada posisi terbuka saat ini.")
        return

    lines = [f"📋 <b>Posisi Terbuka ({len(trades)})</b>", "─" * 30]
    for t in trades:
        current = db.get_latest_price(t["symbol"])
        if current:
            pnl_pct = (current - t["entry_price"]) / t["entry_price"] * 100
            sign    = "+" if pnl_pct >= 0 else ""
            lines.append(
                f"<b>{t['symbol']}</b>  Entry ${t['entry_price']:.4f} | "
                f"Harga ${current:.4f}\n"
                f"  {sign}{pnl_pct:.1f}% | TP1: ${t['tp1_price']:.4f}"
            )
        else:
            lines.append(
                f"<b>{t['symbol']}</b>  Entry ${t['entry_price']:.4f} | "
                f"TP1: ${t['tp1_price']:.4f}"
            )

    send_fn("\n".join(lines))


async def cmd_report(db, send_fn) -> None:
    """Handle /report — trigger laporan mingguan manual."""
    from datetime import timedelta
    from trade_journal.journal import (generate_weekly_report,
                                       detect_performance_gap, format_gap_alert)

    date_to   = datetime.utcnow().date()
    date_from = date_to - timedelta(days=7)

    trades_df = db.get_journal_trades_by_period(str(date_from), str(date_to))
    report    = generate_weekly_report(trades_df, str(date_from), str(date_to))
    send_fn(report)

    # Gap detector pakai 14 hari terakhir
    date_2w    = date_to - timedelta(days=14)
    trades_2w  = db.get_journal_trades_by_period(str(date_2w), str(date_to))
    gap        = detect_performance_gap(trades_2w, db)
    if gap:
        send_fn(format_gap_alert(gap))
```

- [ ] **Step 2: Verifikasi import bot berjalan**

```bash
py -c "from trade_journal.bot import poll_telegram_commands, handle_command; print('bot imports OK')"
```

Expected: `bot imports OK`

- [ ] **Step 3: Commit**

```bash
git add trade_journal/bot.py
git commit -m "feat: add Telegram bot command handlers for trade journal"
```

---

## Task 6: Integration — WebSocket + live_loop

**Files:**
- Modify: `collector/realtime.py`
- Modify: `main.py`

- [ ] **Step 1: Hook reminder ke realtime.py**

Di `collector/realtime.py`, cari fungsi `handle_message()`. Setelah baris `logger.debug(f"✓ Candle closed: ...")`, tambahkan 2 baris berikut:

```python
    # Fire callbacks (signal engine, etc.)
    for callback in _on_candle_close_callbacks:
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(candle)
            else:
                callback(candle)
        except Exception as e:
            logger.error(f"Callback error for {callback.__name__}: {e}")

    # Phase 3: TP/SL reminder check
    try:
        from trade_journal.reminder import check_tp_sl_reminders
        from utils.telegram import send
        await check_tp_sl_reminders(candle, db, send)
    except Exception as e:
        logger.error(f"Reminder check error: {e}")
```

(Tambahkan setelah blok `for callback in _on_candle_close_callbacks` yang sudah ada)

- [ ] **Step 2: Update live_loop() di main.py**

Ganti fungsi `live_loop()` di `main.py` dengan versi berikut:

```python
async def live_loop():
    """
    Main live loop:
    - WebSocket streams 24/7
    - Signal engine runs on every 4H candle close
    - Telegram bot polling for journal commands
    - Weekly P&L report + performance gap detector
    """
    from collector.realtime import on_candle_close, connect_and_stream
    from trade_journal.bot import poll_telegram_commands

    logger.info("Starting APEX live mode...")
    send_system_status("started", "Live scanning all coins 24/7")

    db         = get_db()
    macro_data = fetch_all_macro()
    _state     = {
        "macro":              macro_data,
        "last_macro_update":  0,
        "scan_count":         0,
    }

    @on_candle_close
    async def handle_close(candle: dict):
        """Called every time a 4H candle closes for any coin."""
        symbol = candle["symbol"]
        tf     = candle["timeframe"]

        if tf != "4h":
            return

        logger.info(f"4H candle closed: {symbol} @ {candle['close']:.4f}")
        _state["scan_count"] += 1

        if _state["scan_count"] % 144 == 0:
            logger.info("Refreshing macro data...")
            _state["macro"] = fetch_all_macro()

        macro = _state["macro"]
        f1    = macro["f1_gate"]
        f2    = macro["f2_gate"]

        if not f1["passed"]:
            logger.info(f"F1 GATE: {f1['reason']} — skip scan")
            return

        fear_greed = macro["macro"]["fear_greed"].get("value", 50)

        from signals.engine import score_coin
        result = score_coin(symbol, fear_greed=fear_greed,
                            allowed_tiers=f2["allowed_tiers"])

        if result.get("fired"):
            portfolio_usd = get_portfolio_usd()
            calc = calc_position_size(
                symbol, result["price"],
                result["total_score"],
                portfolio_usd
            )

            if not calc["valid"]:
                logger.warning(f"Trade rejected: {calc['reject_reason']}")
                return

            guard = check_portfolio_guards(symbol)
            if not guard["allowed"]:
                logger.warning(f"Portfolio guard: {guard['reason']}")
                return

            msg = format_trade_for_telegram(calc, result)
            send_signal_alert(msg)
            logger.info(f"🔔 SIGNAL SENT: {symbol} | Score: {result['total_score']}")

    await asyncio.gather(
        connect_and_stream(),
        poll_telegram_commands(db),
        _run_weekly_report_scheduler(db),
    )


async def _run_weekly_report_scheduler(db) -> None:
    """Kirim laporan P&L setiap Senin jam 00:00 UTC."""
    from datetime import timedelta
    from trade_journal.journal import (generate_weekly_report,
                                       detect_performance_gap, format_gap_alert)

    while True:
        now = datetime.utcnow()
        days_ahead = (7 - now.weekday()) % 7 or 7
        next_run   = now.replace(hour=0, minute=0, second=0, microsecond=0) + \
                     timedelta(days=days_ahead)
        wait_secs  = (next_run - now).total_seconds()
        logger.info(f"Weekly report scheduled in {wait_secs/3600:.1f} jam")
        await asyncio.sleep(wait_secs)

        date_to   = datetime.utcnow().date()
        date_from = date_to - timedelta(days=7)

        trades_df = db.get_journal_trades_by_period(str(date_from), str(date_to))
        report    = generate_weekly_report(trades_df, str(date_from), str(date_to))
        send_signal_alert(report)
        logger.info("Weekly report sent")

        # Gap detector (14 hari)
        date_2w   = date_to - timedelta(days=14)
        trades_2w = db.get_journal_trades_by_period(str(date_2w), str(date_to))
        gap       = detect_performance_gap(trades_2w, db)
        if gap:
            send_signal_alert(format_gap_alert(gap))
            logger.warning("Performance gap detected — alert sent")
```

Tambahkan juga import `datetime` di bagian atas `main.py` jika belum ada:

```python
from datetime import datetime
```

- [ ] **Step 3: Jalankan seluruh test suite — pastikan semua pass**

```bash
py -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: minimal `57 passed` (47 lama + 10 baru)

- [ ] **Step 4: Commit final**

```bash
git add collector/realtime.py main.py trade_journal/bot.py
git commit -m "feat: Phase 3 complete — trade journal integrated into live_loop + WebSocket"
```

---

## Verifikasi Manual (setelah deploy)

Setelah `python main.py --run` berjalan di Oracle Cloud:

```
1. Ketik di Telegram:   /open SOLUSDT 142.34 128.10
   Expected response:   ✅ Trade dibuka | TP1: $177.93 ...

2. Ketik:               /status
   Expected response:   📋 Posisi Terbuka (1) — SOLUSDT ...

3. Ketik:               /close SOLUSDT tp1
   Expected response:   ✅ Trade ditutup | P&L: +$XX.XX ...

4. Ketik:               /report
   Expected response:   📊 APEX Weekly Report ...
```
