# APEX Trading System — Phase 3 Design Spec
**Date:** 2026-06-02
**Status:** Approved
**Approach:** Trade Journal dengan Feedback Loop (Pendekatan A — Integrated asyncio)

---

## 1. Konteks & Tujuan

Phase 1+2 selesai: sistem mengirim sinyal Telegram otomatis berdasarkan 7-signal scoring engine + on-chain + narrative + backtest-optimized weights.

**Gap saat ini:** Tidak ada feedback loop. Sistem tidak tahu apakah user membuka trade, berhasil atau gagal, dan apakah performa live sesuai ekspektasi backtest.

**Tujuan Phase 3:** Tutup feedback loop dengan:
1. Trade journal via Telegram bot commands
2. Auto-remind ketika TP/SL tercapai (dari WebSocket)
3. Laporan P&L mingguan dengan signal accuracy breakdown
4. Performance Gap Detector — alert + auto-suggest re-optimize jika live performance jauh di bawah backtest

---

## 2. Arsitektur

Trade journal duduk **di atas** Phase 1+2 sebagai modul baru `trade_journal/`. Diintegrasikan ke dalam `--run` mode sebagai asyncio coroutine tambahan — tidak ada proses baru.

```
main.py --run
├── connect_and_stream()        ← existing WebSocket (Bybit)
├── poll_telegram_commands()    ← NEW: bot polling loop
└── check_tp_sl_reminders()     ← NEW: dipanggil dari candle close handler
```

### File Baru
```
trade_journal/
  __init__.py
  bot.py          — Telegram polling + command handlers (/open, /close, /status, /report)
  journal.py      — P&L calculator, report generator, performance gap detector
  reminder.py     — TP/SL price monitor, hook ke WebSocket candle close

tests/
  test_trade_journal.py
```

### File Dimodifikasi
```
database.py     — tambah tabel journal_trades
main.py         — tambah 2 coroutine ke live_loop()
collector/realtime.py — panggil check_reminders() di candle close handler
```

---

## 3. Database — Tabel Baru

### `journal_trades`
```sql
CREATE TABLE journal_trades (
    id              VARCHAR PRIMARY KEY,     -- UUID
    symbol          VARCHAR NOT NULL,
    entry_price     DOUBLE NOT NULL,
    stop_price      DOUBLE NOT NULL,
    tp1_price       DOUBLE NOT NULL,         -- dihitung otomatis: entry * (1 + stop_pct * 2.5)
    tp2_price       DOUBLE NOT NULL,         -- dihitung otomatis: entry * (1 + stop_pct * 5.0)
    open_time       TIMESTAMP NOT NULL,
    close_time      TIMESTAMP,
    exit_price      DOUBLE,
    exit_reason     VARCHAR,                 -- 'tp1' | 'tp2' | 'stop' | 'manual' | 'timeout'
    pnl_usd         DOUBLE,
    pnl_idr         DOUBLE,
    r_multiple      DOUBLE,
    signal_score    DOUBLE,                  -- total_score dari sinyal APEX terkait
    signal_id       VARCHAR,                 -- FK ke tabel signals (auto-link, bisa NULL)
    status          VARCHAR DEFAULT 'open',  -- 'open' | 'closed'
    notes           VARCHAR                  -- opsional, dari user
);
```

---

## 4. Telegram Bot Commands

### `/open SYMBOL ENTRY STOP`
```
User: /open SOLUSDT 142.34 128.10

Bot:  ✅ Trade dibuka
      SOLUSDT | Entry $142.34 | Stop $128.10
      TP1: $177.93 (+25%) | TP2: $213.51 (+50%)
      Risk: $13.76 (9.7%)
      🔗 Linked ke sinyal score 87 (3 jam lalu)
```

- TP1 dan TP2 dihitung otomatis dari `STOP_LOSS_PCT` per tier di `config.py`
- Auto-link ke sinyal APEX **paling baru** (by timestamp) untuk symbol tersebut dalam 48 jam terakhir
- Jika tidak ada sinyal recent → dicatat tapi `signal_id = NULL`, bot memberitahu

### `/close SYMBOL EXIT`
```
User: /close SOLUSDT tp1
User: /close SOLUSDT stop
User: /close SOLUSDT 177.92     ← harga custom

Bot:  ✅ Trade ditutup
      SOLUSDT | Exit $177.93 (TP1)
      P&L: +$8.79 (+6.2%) | 2.4R
      Hold: 3 hari 4 jam
```

### `/status`
```
Bot:  📋 Posisi Terbuka (2)
      ──────────────────────────────
      SOLUSDT  Entry $142.34 | Harga $155.20
               +$9.16 (+6.4%) | TP1: $177.93
      BTCUSDT  Entry $68,200 | Harga $67,100
               -$7.00 (-1.6%) | Stop: $62,744
```
"Harga" diambil dari candle close terakhir di DuckDB — tidak perlu API call tambahan.

### `/report`
Trigger laporan manual (format sama seperti laporan mingguan otomatis).

---

## 5. Auto-Remind TP/SL

Di-hook ke `on_candle_close` handler yang sudah ada di `collector/realtime.py`:

```python
# Setiap candle 4H close:
await check_tp_sl_reminders(candle, db)
```

**Logic `check_tp_sl_reminders()`:**
```
Untuk setiap trade dengan status='open':
  Jika candle.high >= tp1_price:
      → kirim: "🎯 SOLUSDT nyentuh TP1 ($177.93) — sudah close?\n/close SOLUSDT tp1"
  Jika candle.low <= stop_price:
      → kirim: "🛑 SOLUSDT kena stop ($128.10) — sudah close?\n/close SOLUSDT stop"
  Jika open_time > 7 hari:
      → kirim: "⏰ SOLUSDT sudah 7 hari terbuka — mau di-close?\n/close SOLUSDT [harga]"
```

**Anti-spam:** Setiap reminder disimpan timestamp-nya. Tidak kirim ulang reminder yang sama dalam 4 jam.

---

## 6. Laporan P&L Mingguan

Dikirim otomatis **setiap Senin jam 07:00 WIB** (00:00 UTC).

Format:
```
📊 APEX Weekly Report — 26 Mei – 1 Jun
═══════════════════════════════════════
Trades  : 5  |  Win: 3  |  Loss: 2
Win rate: 60%  |  Avg R: 1.8R
P&L     : +$23.40  (Rp 416,520)

── Per Coin ──────────────────────────
SOLUSDT  2 trade | 2W 0L | +$18.20
BTCUSDT  1 trade | 1W 0L | +$9.50
ARBUSDT  2 trade | 0W 2L | -$4.30

── Best Trade ────────────────────────
SOLUSDT +$18.20 | Score 87 | 2.4R

── Worst Trade ───────────────────────
ARBUSDT -$4.30  | Score 71 | -1.0R

── Signal Accuracy ───────────────────
Score ≥80   → 3 trade | 100% WR ✅
Score 70-79 → 2 trade |   0% WR ⚠️
═══════════════════════════════════════
```

Dijadwalkan via asyncio sleep loop di dalam `live_loop()` — tidak butuh cron eksternal.

---

## 7. Performance Gap Detector

Setiap Senin bersamaan dengan laporan mingguan, sistem membandingkan performa live 2 minggu terakhir vs ekspektasi backtest.

**Ambang batas alert:**
```python
GAP_THRESHOLD_WINRATE = 0.20   # live WR < backtest WR - 20pp → alert
GAP_THRESHOLD_AVG_R   = 0.50   # live avg R < backtest avg R - 0.5R → alert
MIN_SAMPLE_TRADES     = 5      # perlu minimal 5 trade untuk valid
```

**Alert yang dikirim:**
```
⚠️ APEX Performance Gap Detected

Live (2 minggu): 40% WR | 0.9R avg
Backtest ekspektasi: 68% WR | 2.3R avg
Gap: -28pp win rate | -1.4R

Kemungkinan penyebab:
• Market regime berubah (cek F1/F2 gate)
• Sinyal threshold terlalu rendah (score 70-79 semua loss)
• Perlu re-optimize bobot

Saran: jalankan --optimize-weights
```

Jika gap terjadi **2 minggu berturut-turut** → kirim alert lebih urgent:
```
🚨 Gap terjadi 2 minggu berturut-turut.
Jalankan: python main.py --optimize-weights
```
Tidak auto-run — optimizer butuh 30+ menit dan bisa ganggu live scanning.

---

## 8. Integrasi ke `--run` Mode

Di `main.py`, `live_loop()` diupdate:

```python
async def live_loop():
    await asyncio.gather(
        connect_and_stream(),          # existing WebSocket
        poll_telegram_commands(),      # NEW: bot polling
        run_weekly_report_scheduler(), # NEW: laporan + gap detector
    )
```

Tidak ada port/server baru — semua berjalan dalam satu asyncio event loop.

---

## 9. Testing

`tests/test_trade_journal.py` mencakup:
- `test_open_trade_creates_record` — `/open` menyimpan trade ke DB dengan benar
- `test_close_trade_tp1_calculates_pnl` — `/close tp1` hitung P&L + R-multiple benar
- `test_auto_link_signal_within_48h` — auto-link ke sinyal terakhir dalam 48 jam
- `test_auto_link_returns_null_if_no_signal` — NULL jika tidak ada sinyal recent
- `test_tp_sl_reminder_fires_on_candle` — reminder dikirim saat high/low cross level
- `test_reminder_anti_spam_4h` — tidak kirim reminder ulang dalam 4 jam
- `test_performance_gap_detector_triggers` — alert dikirim saat gap > threshold
- `test_performance_gap_below_min_sample` — tidak alert jika < 5 trade

---

## 10. Constraint

- Semua data disimpan di DuckDB yang sudah ada — tidak ada storage baru
- Bot polling menggunakan Telegram REST API yang sudah ada (`utils/telegram.py`) — tidak ada library bot baru
- Tidak ada auto-execute order — tetap manual, sistem hanya catat dan ingatkan
- P&L dihitung dalam USD; IDR conversion pakai `IDR_RATE` dari `.env`
- Semua command hanya diproses jika dari `TELEGRAM_CHAT_ID` yang sudah dikonfigurasi (keamanan)
