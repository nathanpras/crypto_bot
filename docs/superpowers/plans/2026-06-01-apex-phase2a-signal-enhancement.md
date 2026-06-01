# APEX Phase 2A — Signal Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Memperbaiki akurasi sinyal APEX dari 17/19 coin "buta" on-chain menjadi semua coin punya data real, ditambah F3 narrative modifier dan token unlock penalty.

**Architecture:** Phase 1 files dipindahkan ke project root, lalu Phase 2 menambah 3 collector baru, memodifikasi engine.py di satu titik, dan memperbarui Telegram output format. Semua data baru disimpan ke 4 tabel DuckDB baru.

**Tech Stack:** Python 3.10+, DuckDB, ccxt, requests, beautifulsoup4, playwright, loguru, python-dotenv

---

## File Structure

```
CryptoAgent/                          ← project root
├── main.py                           [MODIFY] +3 CLI commands
├── config.py                         [MODIFY] +SECTOR_MAP, +UNLOCK_PENALTIES
├── database.py                       [MODIFY] +4 tabel baru + 6 method baru
├── collector/
│   ├── __init__.py                   [CREATE] empty
│   ├── historical.py                 [COPY from Downloads/files/]
│   ├── macro.py                      [COPY from Downloads/files/]
│   ├── realtime.py                   [COPY from Downloads/files/]
│   ├── onchain_enhanced.py           [CREATE] CoinMetrics + Binance Futures
│   ├── narrative.py                  [CREATE] DeFiLlama TVL
│   └── token_unlocks.py              [CREATE] Tokenomist scraper
├── signals/
│   ├── __init__.py                   [CREATE] empty
│   └── engine.py                     [MODIFY] +3 modifier functions + update score_coin
├── risk/
│   ├── __init__.py                   [CREATE] empty
│   └── manager.py                    [MODIFY] format_trade_for_telegram diperluas
├── utils/
│   ├── __init__.py                   [CREATE] empty
│   └── telegram.py                   [COPY from Downloads/files/]
└── tests/
    ├── conftest.py                   [CREATE] shared fixtures
    ├── test_database_phase2.py       [CREATE] test 4 tabel baru
    ├── test_onchain_enhanced.py      [CREATE] test scoring logic altcoin
    ├── test_narrative.py             [CREATE] test TVL modifier
    ├── test_token_unlocks.py         [CREATE] test penalty logic
    └── test_engine_phase2.py         [CREATE] test score_coin dengan modifiers
```

---

## Task 1: Project Setup — Copy Phase 1 Files & Install Dependencies

**Files:**
- Create: `collector/__init__.py`, `signals/__init__.py`, `risk/__init__.py`, `utils/__init__.py`
- Create: `tests/conftest.py`
- Copy: semua file dari `~/Downloads/files/` ke struktur folder yang benar

- [ ] **Step 1: Buat struktur direktori**

```bash
cd "/Users/jonathanprasetyo/Website Established/CryptoAgent"
mkdir -p collector signals risk utils tests backtesting logs data
touch collector/__init__.py signals/__init__.py risk/__init__.py utils/__init__.py backtesting/__init__.py
```

- [ ] **Step 2: Copy Phase 1 files ke lokasi yang benar**

```bash
cd "/Users/jonathanprasetyo/Website Established/CryptoAgent"
cp ~/Downloads/files/main.py .
cp ~/Downloads/files/config.py .
cp ~/Downloads/files/database.py .
cp ~/Downloads/files/historical.py collector/
cp ~/Downloads/files/macro.py collector/
cp ~/Downloads/files/realtime.py collector/
cp ~/Downloads/files/engine.py signals/
cp ~/Downloads/files/manager.py risk/
cp ~/Downloads/files/telegram.py utils/
```

- [ ] **Step 3: Update import paths di files yang sudah dicopy**

File `signals/engine.py` line 14 — ubah import:
```python
# Dari:
from config import COINS, SIGNAL_WEIGHTS, SIGNAL_THRESHOLD, SIGNAL_STRONG, FILTERS
from database import get_db

# Tetap sama — sudah benar karena semua di root
```

File `risk/manager.py` — tambah di baris paling atas setelah imports yang ada:
```python
import pandas as pd  # pindahkan dari baris 187 ke atas
```

Hapus baris `import pandas as pd` di bawah (baris 187 di file asli).

- [ ] **Step 4: Install semua dependensi**

```bash
cd "/Users/jonathanprasetyo/Website Established/CryptoAgent"
pip3 install ccxt websockets duckdb pandas pandas-ta numpy requests \
    python-dotenv loguru aiohttp schedule colorama \
    vectorbt optuna "beautifulsoup4>=4.12" playwright pytest pytest-asyncio
playwright install chromium
```

Expected output terakhir: `✓ chromium 1xxx installed`

- [ ] **Step 5: Buat .env file**

```bash
cat > .env << 'EOF'
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET=your_secret_here
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
FRED_API_KEY=your_fred_key_here
PAPER_TRADING=true
PORTFOLIO_IDR=10000000
IDR_RATE=17800
LOG_LEVEL=INFO
EOF
```

- [ ] **Step 6: Buat tests/conftest.py**

```python
# tests/conftest.py
import sys
from pathlib import Path
import pytest
import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

@pytest.fixture
def in_memory_db():
    """DuckDB in-memory database untuk testing — tidak menyentuh data production."""
    import duckdb
    conn = duckdb.connect(":memory:")
    return conn

@pytest.fixture
def sample_candles_df():
    """100 candle OHLCV palsu untuk testing sinyal."""
    import numpy as np
    n = 220
    np.random.seed(42)
    price = 100.0
    prices = []
    for _ in range(n):
        price *= (1 + np.random.normal(0, 0.02))
        prices.append(price)

    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="4h"),
        "open":   [p * 0.999 for p in prices],
        "high":   [p * 1.005 for p in prices],
        "low":    [p * 0.995 for p in prices],
        "close":  prices,
        "volume": [abs(np.random.normal(1_000_000, 200_000)) for _ in range(n)],
    })
```

- [ ] **Step 7: Verifikasi Phase 1 masih bisa jalan**

```bash
cd "/Users/jonathanprasetyo/Website Established/CryptoAgent"
python3 -c "from database import get_db; db = get_db(); print('DB OK')"
python3 -c "from config import COINS; print(f'Coins: {len(COINS)}')"
python3 -c "from signals.engine import score_clamp; print('Engine OK')"
```

Expected output:
```
DB OK
Coins: 19
Engine OK
```

- [ ] **Step 8: Commit**

```bash
git init
git add -A
git commit -m "chore: setup phase 2 project structure, copy phase 1 files"
```

---

## Task 2: Database — 4 Tabel Baru + 6 Method Baru

**Files:**
- Modify: `database.py`
- Test: `tests/test_database_phase2.py`

- [ ] **Step 1: Tulis failing tests**

Buat file `tests/test_database_phase2.py`:

```python
# tests/test_database_phase2.py
import pytest
import sys
from pathlib import Path
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database


@pytest.fixture
def db():
    """Fresh in-memory database untuk setiap test."""
    d = Database(":memory:")
    return d


def test_futures_metrics_table_exists(db):
    result = db.conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='futures_metrics'"
    ).fetchone()[0]
    assert result == 1


def test_sector_tvl_table_exists(db):
    result = db.conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='sector_tvl'"
    ).fetchone()[0]
    assert result == 1


def test_token_unlocks_table_exists(db):
    result = db.conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='token_unlocks'"
    ).fetchone()[0]
    assert result == 1


def test_backtest_results_table_exists(db):
    result = db.conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='backtest_results'"
    ).fetchone()[0]
    assert result == 1


def test_upsert_and_get_futures_metrics(db):
    db.upsert_futures_metrics("SOLUSDT", {
        "open_interest":     500_000_000.0,
        "oi_change_24h_pct": 12.5,
        "funding_rate":      -0.02,
        "long_short_ratio":  0.85,
        "liq_long_24h":      2_000_000.0,
        "liq_short_24h":     500_000.0,
    })
    result = db.get_futures_metrics("SOLUSDT")
    assert not result.empty
    assert result.iloc[0]["funding_rate"] == pytest.approx(-0.02)


def test_upsert_and_get_sector_tvl(db):
    db.upsert_sector_tvl("solana", {
        "tvl_usd":        5_000_000_000.0,
        "tvl_change_7d":  8.3,
        "tvl_change_30d": 22.1,
    })
    result = db.get_sector_tvl("solana")
    assert result["tvl_change_30d"] == pytest.approx(22.1)


def test_upsert_and_get_token_unlock(db):
    db.upsert_token_unlock("ARBUSDT", {
        "unlock_date":       date(2026, 7, 1),
        "unlock_amount_usd": 50_000_000.0,
        "unlock_pct_supply": 3.5,
        "category":          "investor",
    })
    upcoming = db.get_upcoming_unlocks("ARBUSDT", days=60)
    assert len(upcoming) == 1
    assert upcoming[0]["unlock_pct_supply"] == pytest.approx(3.5)


def test_get_upcoming_unlocks_empty_when_none(db):
    result = db.get_upcoming_unlocks("BTCUSDT", days=30)
    assert result == []


def test_save_and_get_backtest_result(db):
    db.save_backtest_result({
        "run_id":         "TEST001",
        "weights_json":   '{"trend":0.20,"rsi":0.15}',
        "train_start":    date(2023, 1, 1),
        "train_end":      date(2024, 6, 30),
        "val_start":      date(2024, 7, 1),
        "val_end":        date(2024, 12, 31),
        "train_win_rate": 68.5,
        "val_win_rate":   65.2,
        "train_sharpe":   1.92,
        "val_sharpe":     1.54,
        "total_trades":   147,
        "avg_r":          2.31,
        "max_drawdown":   -12.3,
        "deployed":       False,
    })
    result = db.get_best_backtest()
    assert result["run_id"] == "TEST001"
    assert result["val_sharpe"] == pytest.approx(1.54)
```

- [ ] **Step 2: Jalankan — pastikan FAIL**

```bash
cd "/Users/jonathanprasetyo/Website Established/CryptoAgent"
python3 -m pytest tests/test_database_phase2.py -v 2>&1 | head -30
```

Expected: semua test FAIL dengan `AttributeError` atau `OperationalError`

- [ ] **Step 3: Tambah SCHEMA_PHASE2 dan 6 method baru ke database.py**

Buka `database.py`. Setelah baris penutup `"""` dari `SCHEMA` (setelah baris `);` terakhir dari tabel `portfolio`), tambahkan schema baru:

```python
SCHEMA_PHASE2 = """
CREATE TABLE IF NOT EXISTS futures_metrics (
    symbol              VARCHAR NOT NULL,
    timestamp           TIMESTAMP NOT NULL,
    open_interest       DOUBLE,
    oi_change_24h_pct   DOUBLE,
    funding_rate        DOUBLE,
    long_short_ratio    DOUBLE,
    liq_long_24h        DOUBLE,
    liq_short_24h       DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS sector_tvl (
    sector          VARCHAR NOT NULL,
    date            DATE NOT NULL,
    tvl_usd         DOUBLE,
    tvl_change_7d   DOUBLE,
    tvl_change_30d  DOUBLE,
    PRIMARY KEY (sector, date)
);

CREATE TABLE IF NOT EXISTS token_unlocks (
    symbol              VARCHAR NOT NULL,
    unlock_date         DATE NOT NULL,
    unlock_amount_usd   DOUBLE,
    unlock_pct_supply   DOUBLE,
    category            VARCHAR,
    PRIMARY KEY (symbol, unlock_date)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    run_id          VARCHAR PRIMARY KEY,
    run_date        TIMESTAMP,
    weights_json    VARCHAR,
    train_start     DATE,
    train_end       DATE,
    val_start       DATE,
    val_end         DATE,
    train_win_rate  DOUBLE,
    val_win_rate    DOUBLE,
    train_sharpe    DOUBLE,
    val_sharpe      DOUBLE,
    total_trades    INTEGER,
    avg_r           DOUBLE,
    max_drawdown    DOUBLE,
    deployed        BOOLEAN DEFAULT FALSE
);
"""
```

Di method `_init_schema`, tambah baris `self.conn.execute(SCHEMA_PHASE2)`:

```python
def _init_schema(self):
    self.conn.execute(SCHEMA)
    self.conn.execute(SCHEMA_PHASE2)
```

Tambahkan 6 method baru di class `Database`, setelah method `get_latest_macro`:

```python
# ── Futures Metrics ──────────────────────────────────────
def upsert_futures_metrics(self, symbol: str, data: dict):
    self.conn.execute("""
        INSERT OR REPLACE INTO futures_metrics VALUES (?, now(), ?, ?, ?, ?, ?, ?)
    """, [
        symbol,
        data.get("open_interest"),
        data.get("oi_change_24h_pct"),
        data.get("funding_rate"),
        data.get("long_short_ratio"),
        data.get("liq_long_24h"),
        data.get("liq_short_24h"),
    ])

def get_futures_metrics(self, symbol: str, limit: int = 1) -> pd.DataFrame:
    return self.conn.execute("""
        SELECT * FROM futures_metrics
        WHERE symbol = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, [symbol, limit]).df()

# ── Sector TVL ───────────────────────────────────────────
def upsert_sector_tvl(self, sector: str, data: dict):
    self.conn.execute("""
        INSERT OR REPLACE INTO sector_tvl VALUES (?, CURRENT_DATE, ?, ?, ?)
    """, [
        sector,
        data.get("tvl_usd"),
        data.get("tvl_change_7d"),
        data.get("tvl_change_30d"),
    ])

def get_sector_tvl(self, sector: str) -> dict:
    result = self.conn.execute("""
        SELECT * FROM sector_tvl
        WHERE sector = ?
        ORDER BY date DESC LIMIT 1
    """, [sector]).df()
    if result.empty:
        return {}
    return result.iloc[0].to_dict()

# ── Token Unlocks ────────────────────────────────────────
def upsert_token_unlock(self, symbol: str, data: dict):
    self.conn.execute("""
        INSERT OR REPLACE INTO token_unlocks VALUES (?, ?, ?, ?, ?)
    """, [
        symbol,
        data["unlock_date"],
        data.get("unlock_amount_usd"),
        data.get("unlock_pct_supply"),
        data.get("category"),
    ])

def get_upcoming_unlocks(self, symbol: str, days: int = 30) -> list[dict]:
    result = self.conn.execute("""
        SELECT * FROM token_unlocks
        WHERE symbol = ?
          AND unlock_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL ? DAY
        ORDER BY unlock_date
    """, [symbol, days]).df()
    return result.to_dict("records")

# ── Backtest Results ─────────────────────────────────────
def save_backtest_result(self, data: dict):
    self.conn.execute("""
        INSERT OR REPLACE INTO backtest_results VALUES (
            ?, now(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, [
        data["run_id"], data["weights_json"],
        data["train_start"], data["train_end"],
        data["val_start"], data["val_end"],
        data.get("train_win_rate"), data.get("val_win_rate"),
        data.get("train_sharpe"), data.get("val_sharpe"),
        data.get("total_trades"), data.get("avg_r"),
        data.get("max_drawdown"), data.get("deployed", False),
    ])

def get_best_backtest(self) -> dict:
    result = self.conn.execute("""
        SELECT * FROM backtest_results
        WHERE deployed = TRUE OR val_sharpe IS NOT NULL
        ORDER BY val_sharpe DESC NULLS LAST
        LIMIT 1
    """).df()
    if result.empty:
        return {}
    return result.iloc[0].to_dict()
```

- [ ] **Step 4: Jalankan tests — pastikan PASS**

```bash
python3 -m pytest tests/test_database_phase2.py -v
```

Expected:
```
tests/test_database_phase2.py::test_futures_metrics_table_exists PASSED
tests/test_database_phase2.py::test_sector_tvl_table_exists PASSED
tests/test_database_phase2.py::test_token_unlocks_table_exists PASSED
tests/test_database_phase2.py::test_backtest_results_table_exists PASSED
tests/test_database_phase2.py::test_upsert_and_get_futures_metrics PASSED
tests/test_database_phase2.py::test_upsert_and_get_sector_tvl PASSED
tests/test_database_phase2.py::test_upsert_and_get_token_unlock PASSED
tests/test_database_phase2.py::test_get_upcoming_unlocks_empty_when_none PASSED
tests/test_database_phase2.py::test_save_and_get_backtest_result PASSED

9 passed in 0.XXs
```

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database_phase2.py tests/conftest.py
git commit -m "feat: add 4 Phase 2 database tables and 8 new DB methods"
```

---

## Task 3: Config — Tambah SECTOR_MAP dan UNLOCK_PENALTIES

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Tambah 3 blok baru di akhir `config.py`**

```python
# ─── Phase 2: Sector Mapping (DeFiLlama chain slugs) ──────────
SECTOR_MAP = {
    "BTCUSDT":  "bitcoin",
    "ETHUSDT":  "ethereum",
    "SOLUSDT":  "solana",
    "BNBUSDT":  "bsc",
    "XRPUSDT":  "ripple",
    "ADAUSDT":  "cardano",
    "AVAXUSDT": "avalanche",
    "LINKUSDT": "ethereum",   # Chainlink TVL tracked on ETH
    "DOTUSDT":  "polkadot",
    "TONUSDT":  "ton",
    "ONDOUSDT": "ethereum",   # ONDO/RWA protocol on ETH
    "ARBUSDT":  "arbitrum",
    "OPUSDT":   "optimism",
    "NEARUSDT": "near",
    "INJUSDT":  "injective",
    "SUIUSDT":  "sui",
    "APTUSDT":  "aptos",
    "SEIUSDT":  "sei",
    "POLUSDT":  "polygon",
}

# ─── Phase 2: Narrative Score Modifiers ──────────────────────
NARRATIVE_THRESHOLDS = {
    "strong_up":   20.0,   # TVL 30d change > +20% → +5
    "mild_up":     10.0,   # TVL 30d change > +10% → +2
    "mild_down":  -10.0,   # TVL 30d change < -10% → -3
    "strong_down": -20.0,  # TVL 30d change < -20% → -8
}
NARRATIVE_MODIFIERS = {
    "strong_up":   5,
    "mild_up":     2,
    "neutral":     0,
    "mild_down":  -3,
    "strong_down": -8,
}

# ─── Phase 2: Token Unlock Penalties ─────────────────────────
UNLOCK_PENALTIES = {
    "days_7":         25,   # unlock dalam 7 hari
    "days_14":        20,   # unlock dalam 14 hari
    "days_30":        10,   # unlock dalam 30 hari
    "large_supply":   10,   # tambahan jika unlock_pct_supply >= 5%
}
UNLOCK_LARGE_THRESHOLD = 5.0  # persen supply yang dianggap "besar"

# ─── Phase 2: Binance Futures Scoring Thresholds ─────────────
FUTURES_SCORING = {
    "funding_very_negative":  -0.05,  # funding < ini → sangat bullish (80)
    "funding_negative":       -0.01,  # funding < ini → bullish (70)
    "funding_positive_high":   0.05,  # funding > ini → overleveraged, bearish (20)
    "oi_surge_pct":           10.0,   # OI change > 10% dalam 24h = significant
    "ls_ratio_extreme_short":  0.8,   # L/S ratio < ini = extreme short, squeeze setup
    "ls_ratio_extreme_long":   2.0,   # L/S ratio > ini = crowded longs, risky
}
```

- [ ] **Step 2: Verifikasi import berjalan**

```bash
python3 -c "from config import SECTOR_MAP, UNLOCK_PENALTIES, FUTURES_SCORING; print('Config OK:', len(SECTOR_MAP), 'sectors')"
```

Expected: `Config OK: 19 sectors`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add SECTOR_MAP, NARRATIVE_MODIFIERS, UNLOCK_PENALTIES to config"
```

---

## Task 4: On-Chain Enhanced Collector

**Files:**
- Create: `collector/onchain_enhanced.py`
- Test: `tests/test_onchain_enhanced.py`

- [ ] **Step 1: Tulis failing tests**

Buat `tests/test_onchain_enhanced.py`:

```python
# tests/test_onchain_enhanced.py
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.onchain_enhanced import (
    score_from_futures_data,
    score_from_coinmetrics_data,
    calc_onchain_score_enhanced,
)


def test_score_from_futures_bullish():
    """OI naik + funding negatif + price naik = score tinggi."""
    data = {
        "oi_change_24h_pct": 15.0,
        "funding_rate":      -0.03,
        "long_short_ratio":  0.75,
        "liq_long_24h":      1_000_000,
        "liq_short_24h":     5_000_000,
    }
    score = score_from_futures_data(data)
    assert score >= 75, f"Expected >= 75, got {score}"


def test_score_from_futures_overleveraged():
    """Funding sangat positif = overleveraged longs = score rendah."""
    data = {
        "oi_change_24h_pct": 5.0,
        "funding_rate":      0.08,   # sangat positif
        "long_short_ratio":  2.5,    # terlalu banyak longs
        "liq_long_24h":      100_000,
        "liq_short_24h":     10_000,
    }
    score = score_from_futures_data(data)
    assert score <= 30, f"Expected <= 30, got {score}"


def test_score_from_futures_neutral():
    """Semua data netral = score ~50."""
    data = {
        "oi_change_24h_pct": 0.5,
        "funding_rate":      0.01,
        "long_short_ratio":  1.0,
        "liq_long_24h":      500_000,
        "liq_short_24h":     500_000,
    }
    score = score_from_futures_data(data)
    assert 40 <= score <= 65, f"Expected 40-65, got {score}"


def test_score_from_futures_long_liquidation_cascade():
    """Long liquidation besar = capitulation = potential bottom = bullish."""
    data = {
        "oi_change_24h_pct": -5.0,
        "funding_rate":      0.00,
        "long_short_ratio":  1.0,
        "liq_long_24h":      50_000_000,  # massive long liq
        "liq_short_24h":     1_000_000,
    }
    score = score_from_futures_data(data)
    assert score >= 70, f"Long cascade should be bullish, got {score}"


def test_score_from_coinmetrics_undervalued():
    """MVRV < 1 = undervalued + outflow = sangat bullish."""
    data = {
        "exch_netflow": -5000,   # outflow bullish
        "mvrv_ratio":   0.85,    # undervalued
    }
    score = score_from_coinmetrics_data(data)
    assert score >= 80, f"Expected >= 80, got {score}"


def test_score_from_coinmetrics_overvalued():
    """MVRV > 3 = overvalued + inflow = bearish."""
    data = {
        "exch_netflow": 3000,   # inflow bearish
        "mvrv_ratio":   3.5,    # overvalued
    }
    score = score_from_coinmetrics_data(data)
    assert score <= 25, f"Expected <= 25, got {score}"


def test_calc_onchain_score_enhanced_returns_float():
    """calc_onchain_score_enhanced harus return float 0-100."""
    mock_db = MagicMock()
    mock_db.get_futures_metrics.return_value = pd.DataFrame([{
        "symbol": "SOLUSDT", "timestamp": "2024-01-01",
        "open_interest": 1e9, "oi_change_24h_pct": 10.0,
        "funding_rate": -0.02, "long_short_ratio": 0.9,
        "liq_long_24h": 1e6, "liq_short_24h": 5e5,
    }])

    score = calc_onchain_score_enhanced("SOLUSDT", mock_db)
    assert isinstance(score, float)
    assert 0 <= score <= 100


def test_calc_onchain_score_enhanced_returns_50_when_no_data():
    """Jika tidak ada data futures, return 50 (netral)."""
    mock_db = MagicMock()
    mock_db.get_futures_metrics.return_value = pd.DataFrame()

    score = calc_onchain_score_enhanced("UNKNOWNUSDT", mock_db)
    assert score == 50.0
```

- [ ] **Step 2: Jalankan — pastikan FAIL**

```bash
python3 -m pytest tests/test_onchain_enhanced.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'score_from_futures_data'`

- [ ] **Step 3: Buat `collector/onchain_enhanced.py`**

```python
# collector/onchain_enhanced.py
"""
Enhanced on-chain data collection for all 19 coins.
BTC/ETH: CoinMetrics community API (MVRV, exchange netflow)
All coins: Binance Futures API (OI, funding rate, long/short ratio, liquidations)
"""
import requests
import time
from datetime import date
from loguru import logger

from config import COINS, FUTURES_SCORING
from database import get_db


BINANCE_FUTURES_BASE = "https://fapi.binance.com"
COINMETRICS_BASE = "https://community-api.coinmetrics.io/v4"


# ── Binance Futures Data ──────────────────────────────────────

def fetch_open_interest(symbol: str) -> dict:
    """Fetch current OI + 24h history dari Binance Futures."""
    try:
        # Current OI
        r = requests.get(
            f"{BINANCE_FUTURES_BASE}/fapi/v1/openInterest",
            params={"symbol": symbol}, timeout=10
        )
        r.raise_for_status()
        current_oi = float(r.json()["openInterest"])

        # OI history (24h ago)
        r2 = requests.get(
            f"{BINANCE_FUTURES_BASE}/futures/data/openInterestHist",
            params={"symbol": symbol, "period": "1h", "limit": 25},
            timeout=10
        )
        r2.raise_for_status()
        hist = r2.json()
        if len(hist) >= 24:
            oi_24h_ago = float(hist[-24]["sumOpenInterest"])
            oi_change_pct = (current_oi - oi_24h_ago) / oi_24h_ago * 100
        else:
            oi_change_pct = 0.0

        return {"open_interest": current_oi, "oi_change_24h_pct": oi_change_pct}

    except Exception as e:
        logger.debug(f"OI fetch failed for {symbol}: {e}")
        return {"open_interest": None, "oi_change_24h_pct": 0.0}


def fetch_funding_rate(symbol: str) -> float:
    """Fetch current funding rate dari Binance Futures."""
    try:
        r = requests.get(
            f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex",
            params={"symbol": symbol}, timeout=10
        )
        r.raise_for_status()
        return float(r.json()["lastFundingRate"])
    except Exception as e:
        logger.debug(f"Funding rate fetch failed for {symbol}: {e}")
        return 0.0


def fetch_long_short_ratio(symbol: str) -> float:
    """Fetch long/short account ratio dari Binance Futures."""
    try:
        r = requests.get(
            f"{BINANCE_FUTURES_BASE}/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": "1h", "limit": 1},
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        if data:
            return float(data[0]["longShortRatio"])
        return 1.0
    except Exception as e:
        logger.debug(f"L/S ratio fetch failed for {symbol}: {e}")
        return 1.0


def fetch_liquidations(symbol: str) -> dict:
    """Fetch long + short liquidations dalam 24h dari Binance Futures."""
    try:
        r = requests.get(
            f"{BINANCE_FUTURES_BASE}/futures/data/takerlongshortRatio",
            params={"symbol": symbol, "period": "1h", "limit": 24},
            timeout=10
        )
        r.raise_for_status()
        # Approx liquidations via taker volume imbalance
        data = r.json()
        if not data:
            return {"liq_long_24h": 0.0, "liq_short_24h": 0.0}

        # Use force order endpoint for actual liq data
        r2 = requests.get(
            f"{BINANCE_FUTURES_BASE}/fapi/v1/forceOrders",
            params={"symbol": symbol, "autoCloseType": "LIQUIDATION", "limit": 50},
            timeout=10
        )
        r2.raise_for_status()
        orders = r2.json()

        liq_long  = sum(float(o["origQty"]) * float(o["price"])
                        for o in orders if o.get("side") == "SELL")
        liq_short = sum(float(o["origQty"]) * float(o["price"])
                        for o in orders if o.get("side") == "BUY")

        return {"liq_long_24h": liq_long, "liq_short_24h": liq_short}

    except Exception as e:
        logger.debug(f"Liquidation fetch failed for {symbol}: {e}")
        return {"liq_long_24h": 0.0, "liq_short_24h": 0.0}


def fetch_all_futures_data(symbol: str) -> dict:
    """Fetch semua Binance Futures data untuk satu coin."""
    oi_data   = fetch_open_interest(symbol)
    funding   = fetch_funding_rate(symbol)
    ls_ratio  = fetch_long_short_ratio(symbol)
    liq_data  = fetch_liquidations(symbol)

    return {
        **oi_data,
        "funding_rate":     funding,
        "long_short_ratio": ls_ratio,
        **liq_data,
    }


# ── CoinMetrics Data (BTC + ETH) ──────────────────────────────

def fetch_coinmetrics(asset: str) -> dict:
    """
    Fetch MVRV ratio + exchange netflow dari CoinMetrics community API.
    Asset: 'btc' atau 'eth'. Rate limit: 10 req/menit — includes sleep.
    """
    try:
        r = requests.get(
            f"{COINMETRICS_BASE}/timeseries/asset-metrics",
            params={
                "assets":    asset,
                "metrics":   "CapMVRVCur,FlowNetInvNtv",
                "frequency": "1d",
                "limit":     7,
                "pretty":    "true",
            },
            timeout=15
        )
        r.raise_for_status()
        data = r.json().get("data", [])

        if not data:
            return {}

        latest = data[-1]
        mvrv    = float(latest.get("CapMVRVCur", 0) or 0)
        netflow = float(latest.get("FlowNetInvNtv", 0) or 0)

        logger.debug(f"CoinMetrics {asset}: MVRV={mvrv:.2f}, netflow={netflow:.0f}")
        return {"mvrv_ratio": mvrv, "exch_netflow": netflow}

    except Exception as e:
        logger.warning(f"CoinMetrics fetch failed for {asset}: {e}")
        return {}


# ── Scoring Functions ─────────────────────────────────────────

def score_from_futures_data(data: dict) -> float:
    """
    Hitung on-chain score 0-100 dari Binance Futures data.
    Dipanggil untuk 17 altcoin non-BTC/ETH.
    """
    cfg = FUTURES_SCORING
    score = 50.0

    funding = data.get("funding_rate", 0.0) or 0.0
    oi_chg  = data.get("oi_change_24h_pct", 0.0) or 0.0
    ls      = data.get("long_short_ratio", 1.0) or 1.0
    liq_l   = data.get("liq_long_24h", 0.0) or 0.0
    liq_s   = data.get("liq_short_24h", 0.0) or 0.0

    # Funding rate signal (most predictive)
    if funding < cfg["funding_very_negative"]:
        score = 80   # Shorts paying heavily = strong bullish setup
    elif funding < cfg["funding_negative"]:
        score = 68
    elif funding > cfg["funding_positive_high"]:
        score = 20   # Longs overleveraged = dangerous
    else:
        score = 50

    # OI momentum adjustment
    if oi_chg > cfg["oi_surge_pct"]:
        if funding < 0:
            score = min(score + 10, 90)   # OI up + funding negative = bullish surge
        else:
            score = max(score - 5, 15)    # OI up + positive funding = crowded

    # Long/Short ratio extremes (contrarian)
    if ls < cfg["ls_ratio_extreme_short"]:
        score = min(score + 8, 90)    # Extreme shorts = squeeze potential
    elif ls > cfg["ls_ratio_extreme_long"]:
        score = max(score - 8, 15)    # Extreme longs = crowded, risky

    # Long liquidation cascade = capitulation = potential bottom
    if liq_l > 0 and liq_s > 0:
        total_liq = liq_l + liq_s
        if liq_l / total_liq > 0.80 and total_liq > 5_000_000:
            score = max(score, 75)    # Heavy long liquidation = bottom signal

    return max(0.0, min(100.0, score))


def score_from_coinmetrics_data(data: dict) -> float:
    """
    Hitung on-chain score 0-100 dari CoinMetrics data.
    Dipanggil untuk BTC dan ETH saja.
    """
    if not data:
        return 50.0

    netflow = data.get("exch_netflow", 0) or 0
    mvrv    = data.get("mvrv_ratio", 1.5) or 1.5

    # Exchange netflow score (negative = outflow = bullish)
    if netflow < -5000:      score = 82
    elif netflow < -1000:    score = 70
    elif netflow < 0:        score = 60
    elif netflow < 1000:     score = 45
    else:                    score = 28

    # MVRV adjustment
    if mvrv < 1.0:     score = min(score + 15, 92)   # Undervalued
    elif mvrv < 1.5:   score = min(score + 5,  90)
    elif mvrv > 3.5:   score = max(score - 25, 10)   # Very overvalued
    elif mvrv > 2.5:   score = max(score - 12, 15)

    return max(0.0, min(100.0, float(score)))


def calc_onchain_score_enhanced(symbol: str, db=None) -> float:
    """
    Hitung enhanced on-chain score untuk satu coin.
    BTC/ETH: baca tabel onchain (CoinMetrics data).
    Lainnya: baca tabel futures_metrics (Binance Futures data).
    Return 50.0 jika tidak ada data (graceful fallback).
    """
    if db is None:
        db = get_db()

    is_btc = "BTC" in symbol
    is_eth = "ETH" in symbol and "BTC" not in symbol

    if is_btc or is_eth:
        asset = "btc" if is_btc else "eth"
        try:
            result = db.conn.execute("""
                SELECT exch_netflow, mvrv_ratio FROM onchain
                WHERE asset = ? ORDER BY date DESC LIMIT 7
            """, [asset]).df()
            if result.empty:
                return 50.0
            data = {
                "exch_netflow": result["exch_netflow"].mean(),
                "mvrv_ratio":   result["mvrv_ratio"].iloc[0],
            }
            return score_from_coinmetrics_data(data)
        except Exception:
            return 50.0
    else:
        # Altcoin: baca dari futures_metrics
        metrics_df = db.get_futures_metrics(symbol)
        if metrics_df.empty:
            return 50.0
        row = metrics_df.iloc[0]
        data = {
            "oi_change_24h_pct": row.get("oi_change_24h_pct", 0),
            "funding_rate":      row.get("funding_rate", 0),
            "long_short_ratio":  row.get("long_short_ratio", 1),
            "liq_long_24h":      row.get("liq_long_24h", 0),
            "liq_short_24h":     row.get("liq_short_24h", 0),
        }
        return score_from_futures_data(data)


# ── Collection Runner ─────────────────────────────────────────

def collect_all_onchain(full: bool = False):
    """
    Fetch dan simpan data on-chain untuk semua coin.
    full=False: hanya Binance Futures (cepat, ~30 detik)
    full=True:  Futures + CoinMetrics (semua, ~2 menit)
    """
    db = get_db()

    logger.info(f"Collecting on-chain data (full={full})...")

    # Binance Futures untuk semua 19 coin
    for symbol in COINS:
        try:
            data = fetch_all_futures_data(symbol)
            db.upsert_futures_metrics(symbol, data)
            logger.debug(f"  {symbol}: funding={data.get('funding_rate', 0):.4f}, "
                         f"OI_chg={data.get('oi_change_24h_pct', 0):.1f}%")
            time.sleep(0.2)   # rate limit courtesy
        except Exception as e:
            logger.warning(f"  Failed to collect futures for {symbol}: {e}")

    if full:
        # CoinMetrics untuk BTC dan ETH
        for asset in ["btc", "eth"]:
            try:
                data = fetch_coinmetrics(asset)
                if data:
                    symbol = "BTCUSDT" if asset == "btc" else "ETHUSDT"
                    db.conn.execute("""
                        INSERT OR REPLACE INTO onchain
                        (asset, date, exch_netflow, mvrv_ratio)
                        VALUES (?, CURRENT_DATE, ?, ?)
                    """, [asset, data.get("exch_netflow"), data.get("mvrv_ratio")])
                    logger.info(f"  CoinMetrics {asset}: MVRV={data.get('mvrv_ratio', '?'):.2f}")
                time.sleep(7)   # CoinMetrics rate limit: 10 req/menit
            except Exception as e:
                logger.warning(f"  CoinMetrics failed for {asset}: {e}")

    logger.info("On-chain collection complete.")
```

- [ ] **Step 4: Jalankan tests — pastikan PASS**

```bash
python3 -m pytest tests/test_onchain_enhanced.py -v
```

Expected:
```
tests/test_onchain_enhanced.py::test_score_from_futures_bullish PASSED
tests/test_onchain_enhanced.py::test_score_from_futures_overleveraged PASSED
tests/test_onchain_enhanced.py::test_score_from_futures_neutral PASSED
tests/test_onchain_enhanced.py::test_score_from_futures_long_liquidation_cascade PASSED
tests/test_onchain_enhanced.py::test_score_from_coinmetrics_undervalued PASSED
tests/test_onchain_enhanced.py::test_score_from_coinmetrics_overvalued PASSED
tests/test_onchain_enhanced.py::test_calc_onchain_score_enhanced_returns_float PASSED
tests/test_onchain_enhanced.py::test_calc_onchain_score_enhanced_returns_50_when_no_data PASSED

8 passed in 0.XXs
```

- [ ] **Step 5: Commit**

```bash
git add collector/onchain_enhanced.py tests/test_onchain_enhanced.py config.py
git commit -m "feat: add enhanced on-chain scoring (Binance Futures + CoinMetrics)"
```

---

## Task 5: Narrative Gate Collector (DeFiLlama)

**Files:**
- Create: `collector/narrative.py`
- Test: `tests/test_narrative.py`

- [ ] **Step 1: Tulis failing tests**

Buat `tests/test_narrative.py`:

```python
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
```

- [ ] **Step 2: Jalankan — pastikan FAIL**

```bash
python3 -m pytest tests/test_narrative.py -v 2>&1 | head -10
```

Expected: `ImportError: cannot import name 'calc_sector_modifier'`

- [ ] **Step 3: Buat `collector/narrative.py`**

```python
# collector/narrative.py
"""
DeFiLlama TVL data collection and sector rotation scoring.
API gratis, tidak butuh API key.
"""
import requests
import time
from datetime import date
from loguru import logger

from config import COINS, SECTOR_MAP, NARRATIVE_THRESHOLDS, NARRATIVE_MODIFIERS
from database import get_db


DEFILLAMA_BASE = "https://api.llama.fi"


def fetch_chain_tvl(chain: str) -> dict:
    """
    Fetch TVL historis untuk satu chain dari DeFiLlama.
    Return dict dengan tvl_usd, tvl_change_7d, tvl_change_30d.
    """
    try:
        r = requests.get(
            f"{DEFILLAMA_BASE}/v2/historicalChainTvl/{chain}",
            timeout=15
        )
        r.raise_for_status()
        data = r.json()

        if not data or len(data) < 31:
            logger.debug(f"Insufficient TVL history for {chain}: {len(data)} points")
            return {}

        # Data diurutkan ascending: latest = data[-1]
        current  = float(data[-1]["tvl"])
        week_ago = float(data[-7]["tvl"])   if len(data) >= 7  else current
        month_ago= float(data[-30]["tvl"])  if len(data) >= 30 else current

        change_7d  = (current - week_ago)  / week_ago  * 100 if week_ago  > 0 else 0
        change_30d = (current - month_ago) / month_ago * 100 if month_ago > 0 else 0

        logger.debug(f"TVL {chain}: ${current/1e9:.2f}B | "
                     f"7d: {change_7d:+.1f}% | 30d: {change_30d:+.1f}%")

        return {
            "tvl_usd":        current,
            "tvl_change_7d":  round(change_7d, 2),
            "tvl_change_30d": round(change_30d, 2),
        }

    except Exception as e:
        logger.warning(f"DeFiLlama TVL fetch failed for {chain}: {e}")
        return {}


def collect_all_tvl():
    """Fetch TVL untuk semua sektor unik di SECTOR_MAP. Simpan ke DB."""
    db = get_db()
    sectors_done = set()

    logger.info("Collecting DeFiLlama TVL data...")

    for symbol, chain in SECTOR_MAP.items():
        if chain in sectors_done:
            continue
        sectors_done.add(chain)

        try:
            tvl_data = fetch_chain_tvl(chain)
            if tvl_data:
                db.upsert_sector_tvl(chain, tvl_data)
                logger.debug(f"  {chain}: 30d change = {tvl_data['tvl_change_30d']:+.1f}%")
            time.sleep(0.5)   # DeFiLlama rate limit
        except Exception as e:
            logger.warning(f"  Failed TVL for {chain}: {e}")

    logger.info(f"TVL collection complete. {len(sectors_done)} sectors updated.")


def calc_sector_modifier(tvl_change_30d: float) -> tuple[int, str]:
    """
    Hitung score modifier berdasarkan TVL 30d change.
    Return (modifier_int, label_str).
    """
    t = NARRATIVE_THRESHOLDS
    m = NARRATIVE_MODIFIERS

    if tvl_change_30d >= t["strong_up"]:
        return m["strong_up"], f"strong_up TVL {tvl_change_30d:+.1f}%"
    elif tvl_change_30d >= t["mild_up"]:
        return m["mild_up"], f"mild_up TVL {tvl_change_30d:+.1f}%"
    elif tvl_change_30d <= t["strong_down"]:
        return m["strong_down"], f"strong_down TVL {tvl_change_30d:+.1f}%"
    elif tvl_change_30d <= t["mild_down"]:
        return m["mild_down"], f"mild_down TVL {tvl_change_30d:+.1f}%"
    else:
        return m["neutral"], f"neutral TVL {tvl_change_30d:+.1f}%"


def get_sector_modifier(symbol: str, db=None) -> int:
    """
    Lookup TVL modifier untuk satu coin dari DB.
    Return 0 jika tidak ada data (graceful fallback — jangan block trade).
    """
    if db is None:
        db = get_db()

    chain = SECTOR_MAP.get(symbol)
    if not chain:
        return 0

    tvl_data = db.get_sector_tvl(chain)
    if not tvl_data:
        return 0

    change_30d = tvl_data.get("tvl_change_30d", 0) or 0
    modifier, _ = calc_sector_modifier(change_30d)
    return modifier
```

- [ ] **Step 4: Jalankan tests — pastikan PASS**

```bash
python3 -m pytest tests/test_narrative.py -v
```

Expected: `8 passed in 0.XXs`

- [ ] **Step 5: Commit**

```bash
git add collector/narrative.py tests/test_narrative.py
git commit -m "feat: add DeFiLlama TVL narrative gate with score modifier"
```

---

## Task 6: Token Unlock Scraper

**Files:**
- Create: `collector/token_unlocks.py`
- Test: `tests/test_token_unlocks.py`

- [ ] **Step 1: Tulis failing tests**

Buat `tests/test_token_unlocks.py`:

```python
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
        "unlock_pct_supply": 2.0,   # kecil, tidak trigger large_supply
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
```

- [ ] **Step 2: Jalankan — pastikan FAIL**

```bash
python3 -m pytest tests/test_token_unlocks.py -v 2>&1 | head -10
```

Expected: `ImportError: cannot import name 'calc_unlock_penalty'`

- [ ] **Step 3: Buat `collector/token_unlocks.py`**

```python
# collector/token_unlocks.py
"""
Token unlock calendar scraper dari Tokenomist.ai.
Playwright digunakan karena halaman di-render dengan JavaScript.
Graceful degradation: jika scrape gagal, penalty = 0.
"""
import re
import time
from datetime import date, datetime, timedelta
from loguru import logger

from config import COINS, UNLOCK_PENALTIES, UNLOCK_LARGE_THRESHOLD
from database import get_db


# Mapping symbol → slug di Tokenomist.ai
TOKENOMIST_SLUGS = {
    "SOLUSDT":  "solana",
    "XRPUSDT":  "xrp",
    "BNBUSDT":  "bnb",
    "ADAUSDT":  "cardano",
    "AVAXUSDT": "avalanche-2",
    "LINKUSDT": "chainlink",
    "DOTUSDT":  "polkadot",
    "TONUSDT":  "toncoin",
    "ONDOUSDT": "ondo-finance",
    "ARBUSDT":  "arbitrum",
    "OPUSDT":   "optimism",
    "NEARUSDT": "near",
    "INJUSDT":  "injective-protocol",
    "SUIUSDT":  "sui",
    "APTUSDT":  "aptos",
    "SEIUSDT":  "sei-network",
    "POLUSDT":  "polygon",
    # BTC dan ETH tidak punya scheduled token unlocks
}


def scrape_tokenomist(symbol: str) -> list[dict]:
    """
    Scrape jadwal token unlock dari Tokenomist.ai menggunakan Playwright.
    Return list of unlock dicts. Return [] jika gagal (graceful degradation).
    """
    slug = TOKENOMIST_SLUGS.get(symbol)
    if not slug:
        return []

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page()

            url = f"https://tokenomist.ai/token/{slug}"
            page.goto(url, timeout=30_000, wait_until="networkidle")
            page.wait_for_timeout(3000)   # tunggu JS render

            # Cari tabel unlock events
            unlock_rows = page.query_selector_all("[data-testid='unlock-row'], .unlock-event, tr.unlock")

            results = []
            for row in unlock_rows[:10]:   # maksimum 10 upcoming unlocks
                try:
                    text = row.inner_text()
                    # Parse tanggal (format: "Jun 15, 2026" atau "2026-06-15")
                    date_match = re.search(
                        r'(\d{4}-\d{2}-\d{2})|([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})',
                        text
                    )
                    if not date_match:
                        continue

                    raw_date = date_match.group(0)
                    try:
                        if "-" in raw_date:
                            unlock_dt = datetime.strptime(raw_date, "%Y-%m-%d").date()
                        else:
                            unlock_dt = datetime.strptime(raw_date, "%b %d, %Y").date()
                    except ValueError:
                        continue

                    # Hanya ambil unlock yang akan datang
                    if unlock_dt < date.today():
                        continue

                    # Parse amount (cari angka dengan M/B suffix)
                    amount_match = re.search(r'\$?([\d,.]+)\s*([MB])', text)
                    amount_usd = 0.0
                    if amount_match:
                        num = float(amount_match.group(1).replace(",", ""))
                        mult = 1_000_000 if amount_match.group(2) == "M" else 1_000_000_000
                        amount_usd = num * mult

                    # Parse % supply
                    pct_match = re.search(r'([\d.]+)\s*%', text)
                    pct_supply = float(pct_match.group(1)) if pct_match else 0.0

                    # Parse category
                    category = "unknown"
                    for cat in ["team", "investor", "ecosystem", "community", "treasury"]:
                        if cat in text.lower():
                            category = cat
                            break

                    results.append({
                        "unlock_date":       unlock_dt,
                        "unlock_amount_usd": amount_usd,
                        "unlock_pct_supply": pct_supply,
                        "category":          category,
                    })

                except Exception:
                    continue

            browser.close()
            logger.debug(f"  {symbol}: found {len(results)} upcoming unlocks")
            return results

    except Exception as e:
        logger.warning(f"Tokenomist scrape failed for {symbol}: {e}")
        return []


def collect_all_token_unlocks():
    """
    Scrape token unlock calendar untuk semua coin yang ada di TOKENOMIST_SLUGS.
    Simpan ke DB. Skip gracefully jika scrape gagal.
    """
    db = get_db()
    logger.info("Collecting token unlock calendar...")

    for symbol in TOKENOMIST_SLUGS:
        if symbol not in COINS:
            continue
        try:
            unlocks = scrape_tokenomist(symbol)
            for unlock in unlocks:
                db.upsert_token_unlock(symbol, unlock)
            if unlocks:
                logger.info(f"  {symbol}: {len(unlocks)} unlock events saved")
            time.sleep(2.0)   # jangan spam Tokenomist
        except Exception as e:
            logger.warning(f"  {symbol} unlock collection failed: {e}")

    logger.info("Token unlock collection complete.")


def calc_unlock_penalty(unlocks: list[dict]) -> int:
    """
    Hitung total penalty berdasarkan upcoming unlock events.
    Ambil unlock terdekat dan terapkan tier penalty.
    Large supply (>= UNLOCK_LARGE_THRESHOLD %) menambah +10.
    """
    if not unlocks:
        return 0

    today = date.today()
    max_penalty = 0

    for unlock in unlocks:
        unlock_date = unlock.get("unlock_date")
        if isinstance(unlock_date, str):
            unlock_date = datetime.strptime(unlock_date, "%Y-%m-%d").date()

        if unlock_date is None or unlock_date < today:
            continue

        days_until = (unlock_date - today).days

        # Tentukan base penalty berdasarkan jarak hari
        if days_until <= 7:
            base = UNLOCK_PENALTIES["days_7"]
        elif days_until <= 14:
            base = UNLOCK_PENALTIES["days_14"]
        elif days_until <= 30:
            base = UNLOCK_PENALTIES["days_30"]
        else:
            continue   # lebih dari 30 hari = tidak berpengaruh

        # Tambahan untuk unlock besar
        pct = unlock.get("unlock_pct_supply", 0) or 0
        if pct >= UNLOCK_LARGE_THRESHOLD:
            base += UNLOCK_PENALTIES["large_supply"]

        max_penalty = max(max_penalty, base)

    return max_penalty


def get_unlock_penalty(symbol: str, db=None) -> int:
    """
    Lookup upcoming unlock events dari DB dan hitung penalty.
    Return 0 jika tidak ada data (graceful fallback).
    """
    if db is None:
        db = get_db()

    unlocks = db.get_upcoming_unlocks(symbol, days=30)
    return calc_unlock_penalty(unlocks)
```

- [ ] **Step 4: Jalankan tests — pastikan PASS**

```bash
python3 -m pytest tests/test_token_unlocks.py -v
```

Expected: `7 passed in 0.XXs`

- [ ] **Step 5: Commit**

```bash
git add collector/token_unlocks.py tests/test_token_unlocks.py
git commit -m "feat: add token unlock scraper and penalty calculator"
```

---

## Task 7: Engine Integration — Update score_coin()

**Files:**
- Modify: `signals/engine.py`
- Test: `tests/test_engine_phase2.py`

- [ ] **Step 1: Tulis failing tests**

Buat `tests/test_engine_phase2.py`:

```python
# tests/test_engine_phase2.py
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import date, timedelta
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_db_with_candles():
    """Mock DB yang return candle data cukup untuk scoring."""
    mock = MagicMock()

    n = 220
    np.random.seed(42)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + np.random.normal(0.001, 0.02)))

    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="4h"),
        "open":   [p * 0.999 for p in prices],
        "high":   [p * 1.010 for p in prices],
        "low":    [p * 0.990 for p in prices],
        "close":  prices,
        "volume": [abs(np.random.normal(1_000_000, 200_000)) for _ in range(n)],
    })

    mock.get_candles.return_value = df
    mock.get_futures_metrics.return_value = pd.DataFrame([{
        "symbol": "SOLUSDT", "timestamp": "2024-01-01",
        "open_interest": 1e9, "oi_change_24h_pct": 8.0,
        "funding_rate": -0.015, "long_short_ratio": 0.9,
        "liq_long_24h": 500_000, "liq_short_24h": 1_500_000,
    }])
    mock.get_sector_tvl.return_value = {"tvl_change_30d": 18.5}
    mock.get_upcoming_unlocks.return_value = []
    mock.conn.execute.return_value.df.return_value = pd.DataFrame()
    mock.upsert_signal.return_value = None

    return mock


def test_score_coin_returns_required_fields(mock_db_with_candles):
    """score_coin harus return semua field yang dibutuhkan."""
    from signals.engine import score_coin

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result = score_coin("SOLUSDT", fear_greed=40)

    required = ["symbol", "total_score", "fired", "strong", "signals",
                "price", "regime", "tier"]
    for field in required:
        assert field in result, f"Missing field: {field}"


def test_score_coin_total_score_in_range(mock_db_with_candles):
    """Total score harus dalam range 0-100."""
    from signals.engine import score_coin

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result = score_coin("SOLUSDT", fear_greed=40)

    assert 0 <= result["total_score"] <= 100


def test_score_coin_with_unlock_penalty_reduces_score(mock_db_with_candles):
    """Unlock dalam 7 hari harus kurangi score."""
    from signals.engine import score_coin

    today = date.today()
    mock_db_with_candles.get_upcoming_unlocks.return_value = [{
        "unlock_date":       today + timedelta(days=5),
        "unlock_pct_supply": 2.0,
        "unlock_amount_usd": 10_000_000,
        "category":          "investor",
    }]

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result_with_unlock    = score_coin("SOLUSDT", fear_greed=40)

    mock_db_with_candles.get_upcoming_unlocks.return_value = []

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result_without_unlock = score_coin("SOLUSDT", fear_greed=40)

    assert result_with_unlock["total_score"] <= result_without_unlock["total_score"]


def test_score_coin_sector_modifier_appears_in_result(mock_db_with_candles):
    """sector_modifier harus ada di result dict."""
    from signals.engine import score_coin

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result = score_coin("SOLUSDT", fear_greed=40)

    assert "sector_modifier" in result
    assert "unlock_penalty" in result


def test_score_coin_blocked_tier_returns_zero(mock_db_with_candles):
    """Coin yang tiernya tidak allowed harus return score 0."""
    from signals.engine import score_coin

    with patch("signals.engine.get_db", return_value=mock_db_with_candles):
        result = score_coin("SOLUSDT", fear_greed=40, allowed_tiers=[1])

    assert result["total_score"] == 0
    assert result["fired"] == False
```

- [ ] **Step 2: Jalankan — pastikan FAIL**

```bash
python3 -m pytest tests/test_engine_phase2.py -v 2>&1 | head -20
```

Expected: test `test_score_coin_sector_modifier_appears_in_result` FAIL karena `sector_modifier` belum ada di result

- [ ] **Step 3: Update `signals/engine.py`**

Tambah 3 import baru di bagian atas `signals/engine.py` (setelah import yang sudah ada):

```python
from collector.onchain_enhanced import calc_onchain_score_enhanced
from collector.narrative import get_sector_modifier
from collector.token_unlocks import get_unlock_penalty
```

Ganti function `calc_onchain_score` dengan versi yang panggil enhanced version. Cari function `calc_onchain_score` (baris ~252) dan **ganti seluruhnya** dengan:

```python
def calc_onchain_score(symbol: str, db) -> float:
    """
    On-chain score: BTC/ETH pakai CoinMetrics, altcoin pakai Binance Futures.
    Delegate ke calc_onchain_score_enhanced dari collector/onchain_enhanced.py.
    """
    return calc_onchain_score_enhanced(symbol, db)
```

Di function `score_coin`, cari blok setelah `total = sum(...)` (sekitar baris 392-396) dan ganti dengan:

```python
    # Weighted total (Phase 1)
    total = sum(
        s[f"{key}_score"] * weight
        for key, weight in SIGNAL_WEIGHTS.items()
        if f"{key}_score" in s
    )

    # Phase 2 modifiers
    sector_mod  = get_sector_modifier(symbol, db)
    unlock_pen  = get_unlock_penalty(symbol, db)
    total       = score_clamp(total + sector_mod - unlock_pen)
```

Di blok `result = {...}` (sekitar baris 402), tambahkan 2 field baru:

```python
    result = {
        "symbol":           symbol,
        "tier":             tier,
        "regime":           regime,
        "total_score":      round(total, 1),
        "fired":            fired,
        "strong":           strong,
        "signals":          {k: round(v, 1) for k, v in s.items()},
        "price":            df_4h["close"].iloc[-1],
        "timestamp":        df_4h["timestamp"].iloc[-1],
        "sector_modifier":  sector_mod,    # Phase 2
        "unlock_penalty":   unlock_pen,    # Phase 2
    }
```

- [ ] **Step 4: Jalankan tests — pastikan PASS**

```bash
python3 -m pytest tests/test_engine_phase2.py -v
```

Expected: `5 passed in 0.XXs`

- [ ] **Step 5: Jalankan semua tests sekaligus untuk pastikan tidak ada regresi**

```bash
python3 -m pytest tests/ -v --tb=short
```

Expected: semua test pass, tidak ada yang baru fail.

- [ ] **Step 6: Commit**

```bash
git add signals/engine.py tests/test_engine_phase2.py
git commit -m "feat: integrate Phase 2 modifiers into score_coin (on-chain enhanced + F3 + unlock)"
```

---

## Task 8: Telegram Format Update

**Files:**
- Modify: `risk/manager.py`

- [ ] **Step 1: Ganti `format_trade_for_telegram` di `risk/manager.py`**

Cari dan **ganti seluruh function** `format_trade_for_telegram` dengan versi baru:

```python
def format_trade_for_telegram(calc: dict, signal: dict) -> str:
    """Format trade signal untuk Telegram. Termasuk Phase 2 context section."""
    import os
    from database import get_db
    from config import SECTOR_MAP

    idr_rate = float(os.getenv("IDR_RATE", 17_800))
    risk_idr = calc["risk_usd"] * idr_rate
    pos_idr  = calc["position_usd"] * idr_rate

    paper    = "📄 PAPER TRADE" if os.getenv("PAPER_TRADING", "true").lower() == "true" else "💰 LIVE TRADE"
    strength = "🌪 PERFECT STORM" if signal.get("strong") else "🔔 SIGNAL"

    # Signal breakdown dengan progress bar
    signals  = signal.get("signals", {})
    from config import SIGNAL_WEIGHTS

    def bar(score):
        filled = int(score / 10)
        return "█" * filled + "░" * (10 - filled)

    weight_map = {
        "trend_score":     ("Trend    ", SIGNAL_WEIGHTS["trend_alignment"]),
        "rsi_score":       ("RSI      ", SIGNAL_WEIGHTS["rsi_momentum"]),
        "volume_score":    ("Volume   ", SIGNAL_WEIGHTS["volume_confirm"]),
        "wyckoff_score":   ("Wyckoff  ", SIGNAL_WEIGHTS["wyckoff_phase"]),
        "onchain_score":   ("On-Chain ", SIGNAL_WEIGHTS["onchain_signal"]),
        "macd_score":      ("MACD     ", SIGNAL_WEIGHTS["macd_momentum"]),
        "sentiment_score": ("Sentiment", SIGNAL_WEIGHTS["sentiment_score"]),
    }

    signal_lines = []
    for key, (label, weight) in weight_map.items():
        val = signals.get(key, 0)
        signal_lines.append(
            f"  {label} {bar(val)} {val:.0f}  ({weight*100:.0f}%)"
        )

    # Phase 2 context
    sector_mod  = signal.get("sector_modifier", 0)
    unlock_pen  = signal.get("unlock_penalty", 0)
    score_raw   = signal["total_score"] - sector_mod + unlock_pen
    score_adj   = (f"+{sector_mod}" if sector_mod >= 0 else str(sector_mod))
    unlock_adj  = (f"-{unlock_pen}" if unlock_pen > 0 else "✓ none")

    # Futures context (ambil dari DB jika ada)
    symbol  = calc["symbol"]
    db      = get_db()
    fm_df   = db.get_futures_metrics(symbol)
    if not fm_df.empty:
        fm     = fm_df.iloc[0]
        oi_chg = fm.get("oi_change_24h_pct", 0) or 0
        fund   = fm.get("funding_rate", 0) or 0
        oi_str = f"OI 24h  : {oi_chg:+.1f}%"
        fr_str = f"Funding : {fund:+.4f} {'🟢' if fund < 0 else '🔴'}"
    else:
        oi_str = "OI 24h  : N/A"
        fr_str = "Funding : N/A"

    # TVL context
    chain   = SECTOR_MAP.get(symbol, "")
    tvl_row = db.get_sector_tvl(chain) if chain else {}
    tvl_30d = tvl_row.get("tvl_change_30d", 0) or 0
    tvl_icon = "🟢" if tvl_30d > 5 else "🔴" if tvl_30d < -5 else "⚪"
    tvl_str  = f"Sector  : {chain} TVL {tvl_30d:+.1f}% (30d) {tvl_icon}"

    return f"""
{strength} — {symbol} {paper}
{'═'*42}
Score   : {signal['total_score']:.0f}/100  ({score_adj} sector, -{unlock_pen} unlock)
Regime  : {signal.get('regime','—')}
Tier    : {calc['tier']}

Entry   : ${calc['entry_price']:,.4f}
Stop    : ${calc['stop_price']:,.4f} (-{(1-calc['stop_price']/calc['entry_price'])*100:.1f}%)
TP1     : ${calc['tp1_price']:,.4f} (+{(calc['tp1_price']/calc['entry_price']-1)*100:.1f}%)
TP2     : ${calc['tp2_price']:,.4f} (+{(calc['tp2_price']/calc['entry_price']-1)*100:.1f}%)

R/R     : {calc['rr_ratio']:.1f}:1
Position: ${calc['position_usd']:.2f} (Rp {pos_idr:,.0f})
Risk    : ${calc['risk_usd']:.2f} (Rp {risk_idr:,.0f}) | {calc['risk_pct']:.2f}%

── Signal Breakdown ──────────────────────
{chr(10).join(signal_lines)}

── Context ───────────────────────────────
{tvl_str}
Unlock  : {unlock_adj}
{oi_str}
{fr_str}
    """.strip()
```

- [ ] **Step 2: Verifikasi format dengan dry run**

```bash
python3 -c "
from risk.manager import format_trade_for_telegram
calc = {
    'symbol':'SOLUSDT','tier':2,'entry_price':142.34,'stop_price':128.1,
    'tp1_price':177.9,'tp2_price':227.7,'quantity':0.61,'position_usd':87.5,
    'risk_usd':8.75,'risk_pct':1.5,'rr_ratio':2.5,'portfolio_usd':581.0,
    'size_multiplier':1.0,'valid':True,'reject_reason':None
}
signal = {
    'symbol':'SOLUSDT','total_score':87,'strong':True,'fired':True,
    'regime':'TRENDING_BULL','tier':2,'price':142.34,
    'sector_modifier':5,'unlock_penalty':0,
    'signals':{'trend_score':82,'rsi_score':74,'volume_score':88,
               'wyckoff_score':79,'onchain_score':76,'macd_score':63,'sentiment_score':71}
}
print(format_trade_for_telegram(calc, signal))
"
```

Expected: format lengkap dengan Signal Breakdown dan Context section tercetak tanpa error.

- [ ] **Step 3: Commit**

```bash
git add risk/manager.py
git commit -m "feat: update Telegram format with signal breakdown bars and Phase 2 context"
```

---

## Task 9: CLI Commands Baru di main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Tambah 3 CLI argument dan handler di `main.py`**

Di bagian `# ── Imports ───` (sekitar baris 34), tambahkan:

```python
from collector.onchain_enhanced import collect_all_onchain
from collector.narrative import collect_all_tvl
from collector.token_unlocks import collect_all_token_unlocks
```

Di function `main()`, cari blok `parser.add_argument` terakhir (setelah `--macro`) dan tambahkan:

```python
    parser.add_argument("--collect-onchain", action="store_true",
                        help="Fetch Binance Futures OI/funding + optionally TVL/unlocks")
    parser.add_argument("--full", action="store_true",
                        help="Dipakai dengan --collect-onchain: fetch TVL + token unlocks juga")
    parser.add_argument("--backtest", action="store_true",
                        help="Jalankan backtest di data historis DuckDB")
    parser.add_argument("--from", dest="date_from", default=None,
                        help="Tanggal mulai backtest (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="Tanggal akhir backtest (YYYY-MM-DD)")
    parser.add_argument("--optimize-weights", action="store_true",
                        help="Jalankan Optuna optimizer untuk signal weights")
    parser.add_argument("--trials", type=int, default=300,
                        help="Jumlah Optuna trials (default: 300)")
```

Di bagian handler (`if args.fetch_history:` dst), tambahkan:

```python
    elif args.collect_onchain:
        logger.info("Phase 2: Collecting on-chain data...")
        collect_all_onchain(full=args.full)
        if args.full:
            logger.info("Collecting TVL data...")
            collect_all_tvl()
            logger.info("Collecting token unlock calendar...")
            collect_all_token_unlocks()

    elif args.backtest:
        from backtesting.harness import run_backtest
        run_backtest(date_from=args.date_from, date_to=args.date_to)

    elif args.optimize_weights:
        from backtesting.optimizer import run_optimization
        run_optimization(n_trials=args.trials)
```

Juga update quick start help di bagian `else`:

```python
    else:
        parser.print_help()
        print("\nQuick start:")
        print("  python3 main.py --fetch-history          ← Download 2 tahun data")
        print("  python3 main.py --collect-onchain --full ← Fetch on-chain + TVL + unlocks")
        print("  python3 main.py --scan-once              ← Test satu scan")
        print("  python3 main.py --backtest               ← Validasi strategi")
        print("  python3 main.py --optimize-weights       ← Cari bobot optimal")
        print("  python3 main.py --run                    ← Start live mode")
```

- [ ] **Step 2: Verifikasi argparse berjalan**

```bash
python3 main.py --help
```

Expected: semua 8 argument muncul termasuk `--collect-onchain`, `--backtest`, `--optimize-weights`.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add --collect-onchain, --backtest, --optimize-weights CLI commands"
```

---

## Task 10: Smoke Test End-to-End Phase 2A

- [ ] **Step 1: Jalankan full test suite**

```bash
cd "/Users/jonathanprasetyo/Website Established/CryptoAgent"
python3 -m pytest tests/ -v --tb=short
```

Expected: semua test pass. Minimum 30 tests total.

- [ ] **Step 2: Verifikasi scan menggunakan modifiers baru**

```bash
python3 main.py --scan-once 2>&1 | head -40
```

Expected: output scan muncul, tidak ada `ImportError` atau `AttributeError`.

- [ ] **Step 3: Test collect-onchain (butuh koneksi internet)**

```bash
python3 main.py --collect-onchain 2>&1 | tail -10
```

Expected: log `On-chain collection complete.` tanpa crash. Beberapa coin mungkin gagal jika tidak punya futures market (normal untuk spot-only coins).

- [ ] **Step 4: Commit final**

```bash
git add -A
git commit -m "feat: Phase 2A complete — enhanced on-chain, F3 narrative, token unlock, new CLI"
```

---

## Ringkasan Phase 2A

Setelah Plan A selesai, sistem memiliki:

| Komponen | Status |
|----------|--------|
| On-chain score untuk 17 altcoin (Binance Futures) | ✅ |
| On-chain score BTC/ETH (CoinMetrics) | ✅ |
| F3 Narrative modifier ±8 (DeFiLlama TVL) | ✅ |
| Token unlock penalty 0-35 (Tokenomist.ai) | ✅ |
| 4 tabel DB baru | ✅ |
| Telegram format dengan context section | ✅ |
| CLI: --collect-onchain, --backtest, --optimize-weights | ✅ |

**Lanjutkan ke Plan B** untuk backtesting harness dan Optuna weight optimizer.
