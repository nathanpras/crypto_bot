# APEX Phase 4 — Real-time News + Options Flow Design Spec
**Date:** 2026-06-02
**Status:** Approved

---

## 1. Tujuan

Tambah dua sinyal baru ke APEX:
1. **News sentiment** — CryptoPanic real-time polling tiap 5 menit, hard gate untuk berita kritis
2. **Options flow** — Deribit put/call ratio untuk BTC/ETH sebagai fear indicator

Gap utama yang ditutup: sistem sebelumnya tidak tahu tentang hack/exploit/delisting sampai sudah terlambat.

---

## 2. Arsitektur

```
live_loop() — tambah satu coroutine baru:
  └── poll_news_realtime(db, send)   ← poll CryptoPanic tiap 5 menit

signals/engine.py — tambah setelah Phase 2 modifiers:
  1. news gate  → bisa block total jika berita kritis
  2. news_mod + options_mod → score adjustment
```

---

## 3. Database — 3 Tabel Baru

### `coin_news`
```sql
CREATE TABLE coin_news (
    id           VARCHAR PRIMARY KEY,
    symbol       VARCHAR NOT NULL,
    published_at TIMESTAMP NOT NULL,
    title        VARCHAR,
    sentiment    VARCHAR,   -- 'bullish' | 'bearish' | 'neutral'
    is_critical  BOOLEAN DEFAULT FALSE,
    votes_pos    INTEGER DEFAULT 0,
    votes_neg    INTEGER DEFAULT 0,
    source       VARCHAR
);
```

### `news_blocks`
```sql
CREATE TABLE news_blocks (
    symbol         VARCHAR PRIMARY KEY,
    blocked_at     TIMESTAMP NOT NULL,
    reason         VARCHAR,
    expires_at     TIMESTAMP NOT NULL,   -- blocked_at + 6 jam
    manual_unblock BOOLEAN DEFAULT FALSE
);
```

### `options_metrics`
```sql
CREATE TABLE options_metrics (
    symbol          VARCHAR NOT NULL,
    timestamp       TIMESTAMP NOT NULL,
    put_call_ratio  DOUBLE,
    skew_25d        DOUBLE,
    iv_atm          DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);
```

---

## 4. News Collector (`collector/news.py`)

**Source:** CryptoPanic free API (butuh daftar gratis di cryptopanic.com/developers)
**Env var:** `CRYPTOPANIC_API_KEY` — jika tidak ada, modul berjalan tapi modifier = 0

**Hard gate keywords:**
```python
CRITICAL_KEYWORDS = [
    "hack", "hacked", "exploit", "exploited", "stolen", "breach",
    "delisting", "delisted", "rug", "scam", "fraud",
    "arrested", "sec charges", "shutdown", "suspended", "insolvent"
]
```

**Block duration:** 6 jam auto-expiry. `/unblock SYMBOL` untuk manual override.

**News modifier scoring:**
```
bullish_count >= 3, bearish == 0  → +8
bullish > bearish                 → +4
seimbang / tidak ada berita       →  0
bearish > bullish                 → -8
bearish_count >= 3, bullish == 0  → -15
```

**Poll interval:** 5 menit (1 request batch semua coin)

---

## 5. Options Collector (`collector/options.py`)

**Source:** Deribit public API (tidak butuh key)
**Hanya untuk:** BTCUSDT dan ETHUSDT

**Scoring:**
```
put_call < 0.7  AND skew < -3%   → +10  (greed, bullish)
put_call 0.7–1.0                 → +4
put_call 1.0–1.3                 → -5
put_call > 1.3  AND skew > +5%   → -15  (fear, bearish)
```

**Update:** setiap 4 jam via coroutine terpisah atau cron.

---

## 6. Engine Integration (`signals/engine.py`)

Tambahkan setelah Phase 2 block di `score_coin()`:

```python
# Phase 4: News gate (hard block)
news_check = get_news_gate(symbol, db)
if news_check["blocked"]:
    return {**result, "total_score": 0, "fired": False, "strong": False,
            "blocked_reason": f"NEWS: {news_check['reason']}"}

# Phase 4: News + Options modifiers
news_mod    = news_check["modifier"]
options_mod = get_options_modifier(symbol, db)
total       = score_clamp(total + news_mod + options_mod)
```

---

## 7. Telegram Commands Baru

`/unblock SYMBOL` — lepas news block manual sebelum 6 jam expiry

Alert otomatis saat berita kritis masuk:
```
⛔ SOLUSDT DIBLOKIR — Berita kritis terdeteksi
Judul: "Solana validator exploit reported"
Block hingga: 14:30 WIB (6 jam)
Ketik /unblock SOLUSDT untuk buka manual.
```

---

## 8. Config Baru (`config.py`)

```python
CRITICAL_KEYWORDS = [...]
NEWS_BLOCK_HOURS  = 6
NEWS_POLL_INTERVAL_SEC = 300   # 5 menit

OPTIONS_SCORING = {
    "bullish": {"put_call_max": 0.7, "skew_max": -3.0, "modifier": 10},
    "mild_bullish": {"put_call_max": 1.0, "modifier": 4},
    "mild_bearish": {"put_call_max": 1.3, "modifier": -5},
    "bearish": {"put_call_min": 1.3, "skew_min": 5.0, "modifier": -15},
}
```

---

## 9. Testing (8 tests)

- `test_critical_keyword_triggers_block`
- `test_news_modifier_bullish`
- `test_news_modifier_bearish`
- `test_news_block_expires_after_6h`
- `test_options_modifier_fear_market`
- `test_options_modifier_altcoin_returns_zero`
- `test_engine_blocked_by_news_returns_zero`
- `test_unblock_manual_clears_block`

---

## 10. Env Vars Baru

```
CRYPTOPANIC_API_KEY=   # daftar gratis di cryptopanic.com/developers
```

Jika tidak ada → news collector skip, modifier = 0, tidak crash.
