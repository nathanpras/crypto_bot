# APEX Phase 4 — Real-time News + Options Flow Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tambah real-time news gate (CryptoPanic, poll tiap 5 menit) dan options flow modifier (Deribit put/call ratio BTC/ETH) ke APEX signal engine.

**Architecture:** News polling berjalan sebagai coroutine ke-4 di live_loop(). Hard gate untuk berita kritis (hack/exploit/delisting) dengan 6-jam auto-expiry + manual /unblock. Options modifier untuk BTC/ETH dari Deribit public API.

**Tech Stack:** Python 3.11, DuckDB, asyncio, requests, loguru

---

## File Structure

```
collector/
  news.py           [CREATE] CryptoPanic poller + gate logic
  options.py        [CREATE] Deribit put/call ratio

tests/
  test_phase4.py    [CREATE] 8 unit tests

database.py         [MODIFY] +SCHEMA_PHASE4 + 8 metode baru
config.py           [MODIFY] +CRITICAL_KEYWORDS, NEWS_BLOCK_HOURS, OPTIONS_SCORING
signals/engine.py   [MODIFY] +Phase 4 gate setelah Phase 2 modifiers
trade_journal/bot.py[MODIFY] +/unblock command handler
main.py             [MODIFY] +poll_news_realtime ke live_loop asyncio.gather
```

---

## Task 1: Config + DB Schema

**Files:**
- Modify: `config.py`
- Modify: `database.py`
- Create: `tests/test_phase4.py` (subset)

- [ ] **Step 1: Tambah ke config.py** (append setelah FUTURES_SCORING):

```python
# ─── Phase 4: News Gate ───────────────────────────────────────
CRITICAL_KEYWORDS = [
    "hack", "hacked", "exploit", "exploited", "stolen", "breach",
    "delisting", "delisted", "rug", "scam", "fraud",
    "arrested", "sec charges", "shutdown", "suspended", "insolvent",
]
NEWS_BLOCK_HOURS       = 6
NEWS_POLL_INTERVAL_SEC = 300   # 5 menit

# ─── Phase 4: Options Flow Scoring ───────────────────────────
OPTIONS_SCORING = {
    "strong_bullish": {"pc_max": 0.7,  "skew_max": -3.0, "modifier":  10},
    "mild_bullish":   {"pc_max": 1.0,                    "modifier":   4},
    "neutral":        {"pc_max": 1.3,                    "modifier":   0},
    "mild_bearish":   {"pc_max": 1.3,                    "modifier":  -5},
    "strong_bearish": {"pc_min": 1.3,  "skew_min":  5.0, "modifier": -15},
}
```

- [ ] **Step 2: Tambah SCHEMA_PHASE4 ke database.py** (setelah SCHEMA_PHASE2 string, sebelum class Database):

```python
SCHEMA_PHASE4 = """
CREATE TABLE IF NOT EXISTS coin_news (
    id           VARCHAR PRIMARY KEY,
    symbol       VARCHAR NOT NULL,
    published_at TIMESTAMP NOT NULL,
    title        VARCHAR,
    sentiment    VARCHAR,
    is_critical  BOOLEAN DEFAULT FALSE,
    votes_pos    INTEGER DEFAULT 0,
    votes_neg    INTEGER DEFAULT 0,
    source       VARCHAR
);

CREATE TABLE IF NOT EXISTS news_blocks (
    symbol         VARCHAR PRIMARY KEY,
    blocked_at     TIMESTAMP NOT NULL,
    reason         VARCHAR,
    expires_at     TIMESTAMP NOT NULL,
    manual_unblock BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS options_metrics (
    symbol          VARCHAR NOT NULL,
    timestamp       TIMESTAMP NOT NULL,
    put_call_ratio  DOUBLE,
    skew_25d        DOUBLE,
    iv_atm          DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);
"""
```

- [ ] **Step 3: Tambah `self.conn.execute(SCHEMA_PHASE4)` ke _init_schema()**

- [ ] **Step 4: Tambah 8 metode baru ke class Database** (append setelah get_latest_price):

```python
# ── Phase 4: News & Options ───────────────────────────────────

def upsert_coin_news(self, symbol: str, items: list):
    """Simpan news items ke DB. items = list of dicts."""
    for item in items:
        self.conn.execute("""
            INSERT OR REPLACE INTO coin_news
                (id, symbol, published_at, title, sentiment,
                 is_critical, votes_pos, votes_neg, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            item["id"], symbol,
            item.get("published_at", datetime.utcnow()),
            item.get("title", ""),
            item.get("sentiment", "neutral"),
            item.get("is_critical", False),
            item.get("votes_pos", 0),
            item.get("votes_neg", 0),
            item.get("source", ""),
        ])

def get_recent_news(self, symbol: str, hours: int = 24) -> list:
    """Return news items dalam N jam terakhir untuk symbol."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    rows = self.conn.execute("""
        SELECT id, title, sentiment, is_critical, votes_pos, votes_neg
        FROM coin_news
        WHERE symbol = ? AND published_at >= ?
        ORDER BY published_at DESC
    """, [symbol, cutoff]).fetchall()
    cols = ["id", "title", "sentiment", "is_critical", "votes_pos", "votes_neg"]
    return [dict(zip(cols, r)) for r in rows]

def set_news_block(self, symbol: str, reason: str):
    """Set news block untuk symbol selama NEWS_BLOCK_HOURS."""
    from config import NEWS_BLOCK_HOURS
    now = datetime.utcnow()
    expires = now + timedelta(hours=NEWS_BLOCK_HOURS)
    self.conn.execute("""
        INSERT OR REPLACE INTO news_blocks
            (symbol, blocked_at, reason, expires_at, manual_unblock)
        VALUES (?, ?, ?, ?, FALSE)
    """, [symbol, now, reason, expires])

def is_news_blocked(self, symbol: str) -> dict | None:
    """Return block info jika masih aktif, else None."""
    result = self.conn.execute("""
        SELECT reason, expires_at FROM news_blocks
        WHERE symbol = ? AND expires_at > ? AND manual_unblock = FALSE
    """, [symbol, datetime.utcnow()]).fetchone()
    if result:
        return {"reason": result[0], "expires_at": result[1]}
    return None

def clear_news_block(self, symbol: str):
    """Hapus news block untuk symbol (manual unblock)."""
    self.conn.execute("""
        UPDATE news_blocks SET manual_unblock = TRUE WHERE symbol = ?
    """, [symbol])

def upsert_options_metrics(self, symbol: str, metrics: dict):
    """Simpan options metrics terbaru."""
    self.conn.execute("""
        INSERT OR REPLACE INTO options_metrics
            (symbol, timestamp, put_call_ratio, skew_25d, iv_atm)
        VALUES (?, ?, ?, ?, ?)
    """, [
        symbol, datetime.utcnow(),
        metrics.get("put_call_ratio"),
        metrics.get("skew_25d"),
        metrics.get("iv_atm"),
    ])

def get_latest_options(self, symbol: str) -> dict | None:
    """Return options metrics terbaru untuk symbol."""
    result = self.conn.execute("""
        SELECT put_call_ratio, skew_25d, iv_atm, timestamp
        FROM options_metrics
        WHERE symbol = ?
        ORDER BY timestamp DESC LIMIT 1
    """, [symbol]).fetchone()
    if result:
        return {
            "put_call_ratio": result[0],
            "skew_25d":       result[1],
            "iv_atm":         result[2],
            "timestamp":      result[3],
        }
    return None

def cleanup_old_news(self, days: int = 7):
    """Hapus berita lebih dari N hari yang lalu."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    self.conn.execute(
        "DELETE FROM coin_news WHERE published_at < ?", [cutoff]
    )
```

- [ ] **Step 5: Buat tests/test_phase4.py dengan 3 DB tests:**

```python
import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.duckdb"))


def test_set_and_check_news_block(db):
    """set_news_block harus buat block yang aktif."""
    db.set_news_block("SOLUSDT", "Solana exploit reported")
    block = db.is_news_blocked("SOLUSDT")
    assert block is not None
    assert "exploit" in block["reason"]


def test_clear_news_block(db):
    """clear_news_block harus hapus block."""
    db.set_news_block("BTCUSDT", "test reason")
    db.clear_news_block("BTCUSDT")
    assert db.is_news_blocked("BTCUSDT") is None


def test_upsert_and_get_options_metrics(db):
    """upsert_options_metrics + get_latest_options harus round-trip."""
    db.upsert_options_metrics("BTCUSDT", {
        "put_call_ratio": 1.4, "skew_25d": 6.5, "iv_atm": 0.65
    })
    m = db.get_latest_options("BTCUSDT")
    assert m is not None
    assert abs(m["put_call_ratio"] - 1.4) < 0.001
```

- [ ] **Step 6: Run tests — 3 passed:**
```
py -m pytest tests/test_phase4.py -v
```

- [ ] **Step 7: Run full suite — masih 57 passed:**
```
py -m pytest tests/ --tb=short 2>&1 | tail -5
```

- [ ] **Step 8: Commit:**
```
git add config.py database.py tests/test_phase4.py
git commit -m "feat: Phase 4 DB schema — coin_news, news_blocks, options_metrics"
```

---

## Task 2: collector/news.py

**Files:**
- Create: `collector/news.py`
- Test: `tests/test_phase4.py` (tambah tests)

- [ ] **Step 1: Append 3 tests ke tests/test_phase4.py:**

```python
from collector.news import (
    detect_critical_keywords,
    calc_news_modifier,
    get_news_gate,
)


def test_critical_keyword_triggers_block():
    """Judul dengan kata 'hack' harus trigger critical."""
    assert detect_critical_keywords("Solana network hacked, $50M stolen") is True
    assert detect_critical_keywords("Solana price reaches new ATH") is False


def test_news_modifier_bearish(db):
    """3 berita bearish → modifier -15."""
    for i in range(3):
        db.upsert_coin_news("ETHUSDT", [{
            "id": f"news-{i}", "title": f"ETH bad news {i}",
            "sentiment": "bearish", "is_critical": False,
            "votes_pos": 0, "votes_neg": 5, "source": "test",
            "published_at": datetime.utcnow() - timedelta(hours=1),
        }])
    mod = calc_news_modifier("ETHUSDT", db)
    assert mod <= -8


def test_engine_blocked_by_news_returns_zero(db):
    """get_news_gate harus return blocked=True jika ada active block."""
    db.set_news_block("ARBUSDT", "Arbitrum bridge exploit")
    gate = get_news_gate("ARBUSDT", db)
    assert gate["blocked"] is True
    assert gate["modifier"] == 0
```

- [ ] **Step 2: Jalankan — pastikan FAIL (ImportError):**
```
py -m pytest tests/test_phase4.py -k "keyword or modifier or blocked" -v 2>&1 | head -10
```

- [ ] **Step 3: Buat collector/news.py:**

```python
# collector/news.py
import os
import hashlib
from datetime import datetime, timedelta
import requests
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
BASE_URL = "https://cryptopanic.com/api/v1/posts/"

from config import COINS, CRITICAL_KEYWORDS, NEWS_POLL_INTERVAL_SEC


def detect_critical_keywords(title: str) -> bool:
    """Return True jika judul berita mengandung kata kritis."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in CRITICAL_KEYWORDS)


def fetch_news_batch() -> list[dict]:
    """
    Fetch berita terbaru untuk semua coin dalam satu request.
    Return list of raw news dicts dari CryptoPanic.
    Graceful degradation: return [] jika API key tidak ada atau request gagal.
    """
    if not API_KEY:
        logger.debug("CRYPTOPANIC_API_KEY tidak diset — news dilewati")
        return []

    currencies = ",".join(sym.replace("USDT", "") for sym in COINS.keys())
    try:
        r = requests.get(BASE_URL, params={
            "auth_token":  API_KEY,
            "currencies":  currencies,
            "filter":      "hot",
            "kind":        "news",
            "public":      "true",
        }, timeout=15)
        data = r.json()
        return data.get("results", [])
    except Exception as e:
        logger.error(f"CryptoPanic fetch failed: {e}")
        return []


def parse_news_item(item: dict, symbol: str) -> dict:
    """Normalize satu CryptoPanic news item ke format DB."""
    title = item.get("title", "")
    votes = item.get("votes", {})

    # Determine sentiment dari tags CryptoPanic
    tags     = [t.get("slug", "") for t in item.get("currencies", [])]
    neg_vote = votes.get("negative", 0)
    pos_vote = votes.get("positive", 0)

    if pos_vote > neg_vote * 1.5:
        sentiment = "bullish"
    elif neg_vote > pos_vote * 1.5:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    return {
        "id":           hashlib.md5(f"{symbol}{title}".encode()).hexdigest()[:16],
        "title":        title,
        "sentiment":    sentiment,
        "is_critical":  detect_critical_keywords(title),
        "votes_pos":    pos_vote,
        "votes_neg":    neg_vote,
        "source":       item.get("source", {}).get("domain", ""),
        "published_at": item.get("published_at", datetime.utcnow().isoformat()),
    }


def calc_news_modifier(symbol: str, db) -> float:
    """
    Hitung score modifier berdasarkan sentimen berita 24 jam terakhir.
    Return float: -15 s/d +8.
    """
    news = db.get_recent_news(symbol, hours=24)
    if not news:
        return 0.0

    bullish = sum(1 for n in news if n["sentiment"] == "bullish")
    bearish = sum(1 for n in news if n["sentiment"] == "bearish")

    if bullish >= 3 and bearish == 0:     return  8.0
    if bullish > bearish * 1.5:           return  4.0
    if bearish > bullish * 1.5:           return -8.0
    if bearish >= 3 and bullish == 0:     return -15.0
    return 0.0


def get_news_gate(symbol: str, db) -> dict:
    """
    Return {'blocked': bool, 'reason': str, 'modifier': float}.
    Dipanggil dari signals/engine.py sebelum score dihitung.
    """
    block = db.is_news_blocked(symbol)
    if block:
        return {
            "blocked":  True,
            "reason":   block["reason"],
            "modifier": 0.0,
        }
    return {
        "blocked":  False,
        "reason":   "",
        "modifier": calc_news_modifier(symbol, db),
    }


async def poll_news_realtime(db, send_fn) -> None:
    """
    Coroutine: poll CryptoPanic tiap 5 menit.
    Kirim Telegram alert langsung jika berita kritis terdeteksi.
    """
    import asyncio
    from config import NEWS_POLL_INTERVAL_SEC

    while True:
        try:
            raw_items = fetch_news_batch()

            for item in raw_items:
                # Identifikasi symbol dari currencies di response
                for currency_info in item.get("currencies", []):
                    code   = currency_info.get("code", "").upper()
                    symbol = code + "USDT"

                    if symbol not in COINS:
                        continue

                    parsed = parse_news_item(item, symbol)
                    db.upsert_coin_news(symbol, [parsed])

                    # Hard gate: deteksi berita kritis yang belum diblok
                    if parsed["is_critical"] and not db.is_news_blocked(symbol):
                        db.set_news_block(symbol, parsed["title"])
                        from config import NEWS_BLOCK_HOURS
                        from datetime import timedelta
                        expires_wib = datetime.utcnow() + timedelta(hours=NEWS_BLOCK_HOURS + 7)
                        send_fn(
                            f"⛔ <b>{symbol} DIBLOKIR</b> — Berita kritis\n"
                            f"<i>{parsed['title']}</i>\n"
                            f"Block hingga {NEWS_BLOCK_HOURS} jam ke depan.\n"
                            f"Ketik <code>/unblock {symbol}</code> untuk buka manual."
                        )
                        logger.warning(f"NEWS BLOCK: {symbol} — {parsed['title']}")

            # Cleanup berita lama setiap 100 iterasi (~8 jam)
            if not hasattr(poll_news_realtime, "_count"):
                poll_news_realtime._count = 0
            poll_news_realtime._count += 1
            if poll_news_realtime._count % 100 == 0:
                db.cleanup_old_news(days=7)

        except Exception as e:
            logger.error(f"News poll error: {e}")

        await asyncio.sleep(NEWS_POLL_INTERVAL_SEC)
```

- [ ] **Step 4: Run tests — 6 passed (3 lama + 3 baru):**
```
py -m pytest tests/test_phase4.py -v
```

- [ ] **Step 5: Commit:**
```
git add collector/news.py tests/test_phase4.py
git commit -m "feat: add real-time news collector with hard gate + 5min polling"
```

---

## Task 3: collector/options.py

**Files:**
- Create: `collector/options.py`
- Test: `tests/test_phase4.py` (tambah tests)

- [ ] **Step 1: Append 2 tests ke tests/test_phase4.py:**

```python
from collector.options import calc_options_modifier, get_options_modifier


def test_options_modifier_fear_market(db):
    """Put/call > 1.3 + skew > 5 harus return modifier negatif."""
    db.upsert_options_metrics("BTCUSDT", {
        "put_call_ratio": 1.5, "skew_25d": 7.0, "iv_atm": 0.8
    })
    mod = get_options_modifier("BTCUSDT", db)
    assert mod <= -5


def test_options_modifier_altcoin_returns_zero(db):
    """Altcoin (bukan BTC/ETH) harus return 0."""
    mod = get_options_modifier("SOLUSDT", db)
    assert mod == 0.0
```

- [ ] **Step 2: Buat collector/options.py:**

```python
# collector/options.py
import requests
from datetime import datetime
from loguru import logger

from config import OPTIONS_SCORING

DERIBIT_URL = "https://www.deribit.com/api/v2/public"
OPTIONS_SYMBOLS = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}


def fetch_options_data(currency: str) -> dict | None:
    """
    Ambil data options dari Deribit public API (gratis, tanpa key).
    Return dict dengan put_call_ratio, skew_25d, atau None jika gagal.
    """
    try:
        r = requests.get(
            f"{DERIBIT_URL}/get_book_summary_by_currency",
            params={"currency": currency, "kind": "option"},
            timeout=15,
        )
        data = r.json().get("result", [])
        if not data:
            return None

        put_vol  = sum(d.get("volume", 0) for d in data if "P" in d.get("instrument_name", ""))
        call_vol = sum(d.get("volume", 0) for d in data if "C" in d.get("instrument_name", ""))

        pc_ratio = (put_vol / call_vol) if call_vol > 0 else 1.0

        # 25-delta skew: rata-rata bid_iv put minus call untuk ATM options
        # Simplified: pakai mark_iv spread sebagai proxy
        put_ivs  = [d.get("mark_iv", 0) for d in data if "P" in d.get("instrument_name", "") and d.get("mark_iv")]
        call_ivs = [d.get("mark_iv", 0) for d in data if "C" in d.get("instrument_name", "") and d.get("mark_iv")]
        skew = (sum(put_ivs) / len(put_ivs) - sum(call_ivs) / len(call_ivs)) if put_ivs and call_ivs else 0.0

        # ATM IV (average)
        all_ivs = [d.get("mark_iv", 0) for d in data if d.get("mark_iv")]
        iv_atm  = sum(all_ivs) / len(all_ivs) if all_ivs else 0.0

        return {
            "put_call_ratio": round(pc_ratio, 3),
            "skew_25d":       round(skew, 3),
            "iv_atm":         round(iv_atm / 100, 4),  # convert pct to decimal
        }

    except Exception as e:
        logger.error(f"Deribit fetch failed for {currency}: {e}")
        return None


def calc_options_modifier(put_call_ratio: float, skew_25d: float) -> float:
    """Hitung score modifier dari put/call ratio dan skew."""
    cfg = OPTIONS_SCORING

    if put_call_ratio < cfg["strong_bullish"]["pc_max"] and skew_25d < cfg["strong_bullish"]["skew_max"]:
        return float(cfg["strong_bullish"]["modifier"])
    if put_call_ratio < cfg["mild_bullish"]["pc_max"]:
        return float(cfg["mild_bullish"]["modifier"])
    if put_call_ratio > cfg["strong_bearish"]["pc_min"] and skew_25d > cfg["strong_bearish"]["skew_min"]:
        return float(cfg["strong_bearish"]["modifier"])
    if put_call_ratio > cfg["mild_bearish"]["pc_max"]:
        return float(cfg["mild_bearish"]["modifier"])
    return 0.0


def get_options_modifier(symbol: str, db) -> float:
    """
    Return options score modifier untuk symbol.
    0.0 untuk semua coin selain BTC/ETH.
    """
    if symbol not in OPTIONS_SYMBOLS:
        return 0.0

    metrics = db.get_latest_options(symbol)
    if not metrics:
        return 0.0

    return calc_options_modifier(
        metrics["put_call_ratio"] or 1.0,
        metrics["skew_25d"] or 0.0,
    )


def collect_all_options(db) -> None:
    """Fetch dan simpan options metrics untuk BTC + ETH."""
    for symbol, currency in OPTIONS_SYMBOLS.items():
        data = fetch_options_data(currency)
        if data:
            db.upsert_options_metrics(symbol, data)
            logger.info(f"Options {symbol}: P/C={data['put_call_ratio']:.2f} "
                        f"skew={data['skew_25d']:.2f}")
        else:
            logger.warning(f"Options fetch failed for {symbol}")
```

- [ ] **Step 3: Run tests — 8 passed:**
```
py -m pytest tests/test_phase4.py -v
```

- [ ] **Step 4: Commit:**
```
git add collector/options.py tests/test_phase4.py
git commit -m "feat: add Deribit options flow collector for BTC/ETH"
```

---

## Task 4: Engine Integration + /unblock Command + main.py

**Files:**
- Modify: `signals/engine.py`
- Modify: `trade_journal/bot.py`
- Modify: `main.py`

- [ ] **Step 1: Update signals/engine.py**

Tambah 2 import di atas (setelah existing imports):
```python
from collector.news import get_news_gate
from collector.options import get_options_modifier
```

Cari blok Phase 2 modifiers di `score_coin()`:
```python
    # Phase 2 modifiers
    sector_mod  = get_sector_modifier(symbol, db)
    unlock_pen  = get_unlock_penalty(symbol, db)
    total       = score_clamp(total + sector_mod - unlock_pen)
```

Ganti dengan:
```python
    # Phase 2 modifiers
    sector_mod  = get_sector_modifier(symbol, db)
    unlock_pen  = get_unlock_penalty(symbol, db)
    total       = score_clamp(total + sector_mod - unlock_pen)

    # Phase 4: News hard gate
    news_check = get_news_gate(symbol, db)
    if news_check["blocked"]:
        return {
            "symbol":         symbol,
            "tier":           tier,
            "regime":         "BLOCKED",
            "total_score":    0.0,
            "fired":          False,
            "strong":         False,
            "signals":        {},
            "price":          df_4h["close"].iloc[-1],
            "timestamp":      df_4h["timestamp"].iloc[-1],
            "sector_modifier": sector_mod,
            "unlock_penalty":  unlock_pen,
            "news_modifier":   0.0,
            "options_modifier": 0.0,
            "blocked_reason": f"NEWS: {news_check['reason']}",
        }

    # Phase 4: Score modifiers
    news_mod    = news_check["modifier"]
    options_mod = get_options_modifier(symbol, db)
    total       = score_clamp(total + news_mod + options_mod)
```

Update juga result dict (setelah `total = score_clamp(...)`) — tambah 2 key baru:
```python
    result = {
        ...existing keys...,
        "sector_modifier":  sector_mod,
        "unlock_penalty":   unlock_pen,
        "news_modifier":    news_mod,       # tambah
        "options_modifier": options_mod,    # tambah
    }
```

- [ ] **Step 2: Tambah /unblock ke trade_journal/bot.py**

Di fungsi `handle_command()`, tambah case baru setelah `/report`:
```python
    elif cmd == "/unblock":
        if len(parts) < 2:
            send_fn("❓ Format: <code>/unblock SYMBOL</code>\n"
                    "Contoh: <code>/unblock SOLUSDT</code>")
            return
        await cmd_unblock(parts[1].upper(), db, send_fn)
```

Tambah fungsi baru di bawah `cmd_report`:
```python
async def cmd_unblock(symbol: str, db, send_fn) -> None:
    """Handle /unblock — lepas news block manual."""
    block = db.is_news_blocked(symbol)
    if not block:
        send_fn(f"ℹ️ {symbol} tidak sedang diblokir.")
        return
    db.clear_news_block(symbol)
    send_fn(
        f"✅ <b>{symbol} news block dilepas</b>\n"
        f"Alasan sebelumnya: <i>{block['reason']}</i>\n"
        f"Sinyal kembali aktif."
    )
    logger.info(f"Manual unblock: {symbol}")
```

- [ ] **Step 3: Update main.py live_loop()**

Di fungsi `live_loop()`, cari `await asyncio.gather(...)` dan tambah coroutine ke-4:

```python
    await asyncio.gather(
        connect_and_stream(),
        poll_telegram_commands(db),
        _run_weekly_report_scheduler(db),
        _poll_news_loop(db),               # tambah baris ini
    )
```

Tambah fungsi `_poll_news_loop` setelah `_run_weekly_report_scheduler`:
```python
async def _poll_news_loop(db) -> None:
    """Wrapper untuk poll_news_realtime di live_loop."""
    from collector.news import poll_news_realtime
    from utils.telegram import send
    await poll_news_realtime(db, send)
```

- [ ] **Step 4: Run full test suite — semua pass:**
```
py -m pytest tests/ -v --tb=short 2>&1 | tail -10
```
Expected: 65 passed (57 lama + 8 baru)

- [ ] **Step 5: Verifikasi imports:**
```
py -c "
from collector.news import get_news_gate, poll_news_realtime
from collector.options import get_options_modifier, collect_all_options
from signals.engine import score_coin
print('Phase 4 imports OK')
"
```

- [ ] **Step 6: Commit:**
```
git add signals/engine.py trade_journal/bot.py main.py
git commit -m "feat: Phase 4 complete — news gate + options modifier + /unblock command"
```
