# APEX Phase 5 — Social Sentiment + Whale Proxy Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tambah dua sinyal baru: (1) social sentiment via CoinGecko community data untuk semua 19 coin, (2) whale accumulation proxy via exchange netflow (BTC/ETH dari CoinMetrics) + OI anomaly detection (altcoin dari futures_metrics yang sudah ada).

**Architecture:** Dua collector baru sebagai score modifier di engine.py, mengikuti pola Phase 2/4. Social update harian. Whale proxy baca dari tabel DB yang sudah ada — tidak butuh API baru untuk altcoin.

**Tech Stack:** Python 3.11, DuckDB, requests, loguru. CoinGecko free API (tanpa key).

---

## File Structure

```
collector/
  social.py         [CREATE] CoinGecko community data — semua 19 coin
  whale.py          [CREATE] Exchange netflow + OI anomaly whale proxy

tests/
  test_phase5.py    [CREATE] 8 unit tests

database.py         [MODIFY] +SCHEMA_PHASE5 (social_metrics table) + 4 metode
config.py           [MODIFY] +COINGECKO_MAP, SOCIAL_SCORING, WHALE_SCORING
signals/engine.py   [MODIFY] +social_mod + whale_mod setelah Phase 4 modifiers
main.py             [MODIFY] +collect_all_social ke CLI --collect-onchain --full
```

---

## Task 1: Config + DB Schema

**Files:** `config.py`, `database.py`, `tests/test_phase5.py`

- [ ] **Step 1: Append ke config.py** (setelah OPTIONS_SCORING):

```python
# ─── Phase 5: CoinGecko Social Map ───────────────────────────
COINGECKO_MAP = {
    "BTCUSDT":  "bitcoin",
    "ETHUSDT":  "ethereum",
    "SOLUSDT":  "solana",
    "XRPUSDT":  "ripple",
    "BNBUSDT":  "binancecoin",
    "ADAUSDT":  "cardano",
    "AVAXUSDT": "avalanche-2",
    "LINKUSDT": "chainlink",
    "DOTUSDT":  "polkadot",
    "TONUSDT":  "the-open-network",
    "ONDOUSDT": "ondo-finance",
    "ARBUSDT":  "arbitrum",
    "OPUSDT":   "optimism",
    "NEARUSDT": "near",
    "INJUSDT":  "injective-protocol",
    "SUIUSDT":  "sui",
    "APTUSDT":  "aptos",
    "SEIUSDT":  "sei-network",
    "POLUSDT":  "matic-network",
}

SOCIAL_SCORING = {
    "twitter_growth_strong":  {"threshold":  5.0, "modifier":  4},
    "reddit_growth_strong":   {"threshold": 10.0, "modifier":  2},
    "github_active":          {"threshold": 50,   "modifier":  2},
    "twitter_decline":        {"threshold": -5.0, "modifier": -3},
    "all_declining":          {"modifier": -5},
}

# ─── Phase 5: Whale Proxy Thresholds ─────────────────────────
WHALE_SCORING = {
    # BTC/ETH netflow (BTC units / ETH units per 7 days)
    "btc_outflow_bullish":   -1000,   # > 1000 BTC keluar exchange = akumulasi
    "btc_inflow_bearish":     1000,   # > 1000 BTC masuk exchange = distribusi
    "eth_outflow_bullish":  -10000,
    "eth_inflow_bearish":    10000,
    # Altcoin OI anomaly (% change 7 hari)
    "oi_surge_bullish":      20.0,    # OI naik > 20% + funding netral = akumulasi
    "oi_drop_bearish":      -20.0,    # OI turun > 20% + harga flat = distribusi
    "funding_neutral_max":    0.02,   # funding dianggap netral jika < ini
}
```

- [ ] **Step 2: Tambah SCHEMA_PHASE5 ke database.py** (setelah SCHEMA_PHASE4):

```python
SCHEMA_PHASE5 = """
CREATE TABLE IF NOT EXISTS social_metrics (
    symbol              VARCHAR NOT NULL,
    date                DATE NOT NULL,
    twitter_followers   BIGINT,
    twitter_change_30d  DOUBLE,
    reddit_subscribers  BIGINT,
    reddit_change_30d   DOUBLE,
    github_commits_4w   INTEGER,
    telegram_members    BIGINT,
    social_score        DOUBLE,
    PRIMARY KEY (symbol, date)
);
"""
```

- [ ] **Step 3: Tambah `self.conn.execute(SCHEMA_PHASE5)` sebagai baris ke-5 di _init_schema()**

- [ ] **Step 4: Tambah 4 metode ke class Database** (setelah cleanup_old_news):

```python
# ── Phase 5: Social & Whale ───────────────────────────────────

def upsert_social_metrics(self, symbol: str, metrics: dict):
    """Simpan social metrics harian per coin."""
    from datetime import date
    self.conn.execute("""
        INSERT OR REPLACE INTO social_metrics
            (symbol, date, twitter_followers, twitter_change_30d,
             reddit_subscribers, reddit_change_30d,
             github_commits_4w, telegram_members, social_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        symbol, date.today(),
        metrics.get("twitter_followers"),
        metrics.get("twitter_change_30d"),
        metrics.get("reddit_subscribers"),
        metrics.get("reddit_change_30d"),
        metrics.get("github_commits_4w"),
        metrics.get("telegram_members"),
        metrics.get("social_score", 0.0),
    ])

def get_latest_social(self, symbol: str) -> dict | None:
    """Return social metrics terbaru untuk symbol."""
    result = self.conn.execute("""
        SELECT twitter_followers, twitter_change_30d,
               reddit_subscribers, reddit_change_30d,
               github_commits_4w, social_score, date
        FROM social_metrics
        WHERE symbol = ?
        ORDER BY date DESC LIMIT 1
    """, [symbol]).fetchone()
    if result:
        return {
            "twitter_followers":  result[0],
            "twitter_change_30d": result[1],
            "reddit_subscribers": result[2],
            "reddit_change_30d":  result[3],
            "github_commits_4w":  result[4],
            "social_score":       result[5],
            "date":               result[6],
        }
    return None

def get_whale_netflow_7d(self, symbol: str) -> float | None:
    """
    Return 7-day cumulative exchange netflow untuk BTC/ETH.
    Positif = net inflow (selling). Negatif = net outflow (accumulation).
    """
    asset = "BTC" if symbol == "BTCUSDT" else "ETH"
    cutoff = datetime.utcnow() - timedelta(days=7)
    result = self.conn.execute("""
        SELECT SUM(exch_netflow)
        FROM onchain
        WHERE asset = ? AND date >= ?
    """, [asset, cutoff.date()]).fetchone()
    return float(result[0]) if result and result[0] is not None else None

def get_oi_change_7d(self, symbol: str) -> dict | None:
    """
    Return OI change % dan avg funding rate 7 hari terakhir untuk altcoin.
    """
    cutoff = datetime.utcnow() - timedelta(days=7)
    result = self.conn.execute("""
        SELECT
            FIRST(open_interest) AS oi_start,
            LAST(open_interest)  AS oi_end,
            AVG(funding_rate)    AS avg_funding
        FROM futures_metrics
        WHERE symbol = ? AND timestamp >= ?
        ORDER BY timestamp
    """, [symbol, cutoff]).fetchone()
    if result and result[0] and result[1] and result[0] > 0:
        oi_change_pct = (result[1] - result[0]) / result[0] * 100
        return {
            "oi_change_pct": round(oi_change_pct, 2),
            "avg_funding":   round(float(result[2] or 0), 5),
        }
    return None
```

- [ ] **Step 5: Buat tests/test_phase5.py:**

```python
import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta, date

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.duckdb"))


def test_upsert_and_get_social_metrics(db):
    """Social metrics harus tersimpan dan bisa diambil."""
    db.upsert_social_metrics("SOLUSDT", {
        "twitter_followers": 500000, "twitter_change_30d": 8.5,
        "reddit_subscribers": 120000, "reddit_change_30d": 3.2,
        "github_commits_4w": 85, "telegram_members": 45000,
        "social_score": 6.0,
    })
    m = db.get_latest_social("SOLUSDT")
    assert m is not None
    assert m["twitter_followers"] == 500000
    assert abs(m["twitter_change_30d"] - 8.5) < 0.01


def test_get_latest_social_returns_none_if_no_data(db):
    """Return None jika belum ada data social."""
    assert db.get_latest_social("XRPUSDT") is None
```

- [ ] **Step 6: Run — 2 passed:**
```
py -m pytest tests/test_phase5.py -v
```

- [ ] **Step 7: Full suite — 67 passed:**
```
py -m pytest tests/ --tb=short 2>&1 | tail -5
```

- [ ] **Step 8: Commit:**
```
git add config.py database.py tests/test_phase5.py
git commit -m "feat: Phase 5 DB schema + config — social_metrics, whale thresholds"
```

---

## Task 2: collector/social.py

**Files:** `collector/social.py`, `tests/test_phase5.py`

- [ ] **Step 1: Append 3 tests ke test_phase5.py:**

```python
from collector.social import calc_social_score, get_social_modifier


def test_calc_social_score_bullish():
    """Twitter growth > 5% + active github → skor positif."""
    score = calc_social_score(
        twitter_change_30d=7.0,
        reddit_change_30d=5.0,
        github_commits_4w=80,
    )
    assert score > 0


def test_calc_social_score_bearish():
    """Twitter turun > 5% + tidak ada dev activity → skor negatif."""
    score = calc_social_score(
        twitter_change_30d=-7.0,
        reddit_change_30d=-5.0,
        github_commits_4w=0,
    )
    assert score < 0


def test_get_social_modifier_no_data_returns_zero(db):
    """Return 0 jika tidak ada data social di DB."""
    mod = get_social_modifier("DOTUSDT", db)
    assert mod == 0.0
```

- [ ] **Step 2: Jalankan — FAIL (ImportError):**
```
py -m pytest tests/test_phase5.py -k "social" -v 2>&1 | head -10
```

- [ ] **Step 3: Buat collector/social.py:**

```python
# collector/social.py
import time
import requests
from loguru import logger
from config import COINGECKO_MAP, SOCIAL_SCORING

BASE_URL = "https://api.coingecko.com/api/v3/coins"


def fetch_social_data(coingecko_id: str) -> dict | None:
    """
    Ambil community + developer data dari CoinGecko free API.
    Rate limit: ~30 req/menit. Sleep 2s antar request.
    """
    try:
        r = requests.get(
            f"{BASE_URL}/{coingecko_id}",
            params={
                "localization":     "false",
                "tickers":          "false",
                "market_data":      "false",
                "community_data":   "true",
                "developer_data":   "true",
                "sparkline":        "false",
            },
            timeout=15,
            headers={"Accept": "application/json"},
        )
        if r.status_code == 429:
            logger.warning(f"CoinGecko rate limit hit, sleeping 60s")
            time.sleep(60)
            return None
        data = r.json()
        comm = data.get("community_data", {})
        dev  = data.get("developer_data", {})
        return {
            "twitter_followers":  comm.get("twitter_followers", 0) or 0,
            "reddit_subscribers": comm.get("reddit_subscribers", 0) or 0,
            "telegram_members":   comm.get("telegram_channel_user_count", 0) or 0,
            "github_commits_4w":  dev.get("commit_count_4_weeks", 0) or 0,
        }
    except Exception as e:
        logger.error(f"CoinGecko fetch failed for {coingecko_id}: {e}")
        return None


def calc_social_score(twitter_change_30d: float,
                      reddit_change_30d: float,
                      github_commits_4w: int) -> float:
    """
    Hitung social score dari growth metrics.
    Return float: -5 to +8.
    """
    score = 0.0
    cfg   = SOCIAL_SCORING

    if twitter_change_30d >= cfg["twitter_growth_strong"]["threshold"]:
        score += cfg["twitter_growth_strong"]["modifier"]
    elif twitter_change_30d <= cfg["twitter_decline"]["threshold"]:
        score += cfg["twitter_decline"]["modifier"]

    if reddit_change_30d >= cfg["reddit_growth_strong"]["threshold"]:
        score += cfg["reddit_growth_strong"]["modifier"]

    if github_commits_4w >= cfg["github_active"]["threshold"]:
        score += cfg["github_active"]["modifier"]

    # All declining: extra penalty
    if (twitter_change_30d < 0 and reddit_change_30d < 0
            and github_commits_4w == 0):
        score += cfg["all_declining"]["modifier"]

    return max(-5.0, min(8.0, score))


def get_social_modifier(symbol: str, db) -> float:
    """Return social score modifier. 0 jika belum ada data."""
    metrics = db.get_latest_social(symbol)
    if not metrics:
        return 0.0
    return float(metrics.get("social_score", 0.0))


def collect_all_social(db) -> None:
    """
    Fetch dan simpan social metrics untuk semua 19 coin.
    Dipanggil dari --collect-onchain --full (sekali sehari).
    """
    for symbol, cg_id in COINGECKO_MAP.items():
        data = fetch_social_data(cg_id)
        if not data:
            time.sleep(2)
            continue

        # Hitung perubahan vs data sebelumnya
        prev = db.get_latest_social(symbol)
        prev_tw = prev["twitter_followers"] if prev else data["twitter_followers"]
        prev_rd = prev["reddit_subscribers"] if prev else data["reddit_subscribers"]

        tw_change = ((data["twitter_followers"] - prev_tw) / prev_tw * 100
                     if prev_tw and prev_tw > 0 else 0.0)
        rd_change = ((data["reddit_subscribers"] - prev_rd) / prev_rd * 100
                     if prev_rd and prev_rd > 0 else 0.0)

        score = calc_social_score(tw_change, rd_change, data["github_commits_4w"])

        db.upsert_social_metrics(symbol, {
            **data,
            "twitter_change_30d": round(tw_change, 2),
            "reddit_change_30d":  round(rd_change, 2),
            "social_score":       score,
        })
        logger.info(f"Social {symbol}: tw_chg={tw_change:+.1f}% "
                    f"gh={data['github_commits_4w']} score={score:+.1f}")
        time.sleep(2)   # respect rate limit
```

- [ ] **Step 4: Run — 5 passed:**
```
py -m pytest tests/test_phase5.py -v
```

- [ ] **Step 5: Full suite — 70 passed:**
```
py -m pytest tests/ --tb=short 2>&1 | tail -5
```

- [ ] **Step 6: Commit:**
```
git add collector/social.py tests/test_phase5.py
git commit -m "feat: add CoinGecko social sentiment collector"
```

---

## Task 3: collector/whale.py

**Files:** `collector/whale.py`, `tests/test_phase5.py`

- [ ] **Step 1: Append 3 tests ke test_phase5.py:**

```python
from collector.whale import calc_whale_score, get_whale_modifier


def test_whale_score_btc_accumulation(db):
    """BTC net outflow dari exchange = akumulasi = skor positif."""
    # Simulasi: netflow negatif (outflow) di tabel onchain
    from datetime import date, timedelta
    for i in range(7):
        d = date.today() - timedelta(days=i)
        db.conn.execute("""
            INSERT OR REPLACE INTO onchain
                (asset, date, exch_inflow, exch_outflow, exch_netflow,
                 mvrv_ratio, nupl, active_addr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ["BTC", d, 500, 800, -300, 1.5, 0.4, 900000])
    score = calc_whale_score("BTCUSDT", db)
    assert score > 0


def test_whale_modifier_altcoin_no_data_returns_zero(db):
    """Altcoin tanpa data futures_metrics harus return 0."""
    mod = get_whale_modifier("NEARUSDT", db)
    assert mod == 0.0


def test_whale_score_btc_distribution(db):
    """BTC net inflow ke exchange = distribusi = skor negatif."""
    from datetime import date, timedelta
    for i in range(7):
        d = date.today() - timedelta(days=i)
        db.conn.execute("""
            INSERT OR REPLACE INTO onchain
                (asset, date, exch_inflow, exch_outflow, exch_netflow,
                 mvrv_ratio, nupl, active_addr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ["BTC", d, 2000, 500, 1500, 2.5, 0.7, 850000])
    score = calc_whale_score("BTCUSDT", db)
    assert score < 0
```

- [ ] **Step 2: Jalankan — FAIL:**
```
py -m pytest tests/test_phase5.py -k "whale" -v 2>&1 | head -10
```

- [ ] **Step 3: Buat collector/whale.py:**

```python
# collector/whale.py
from loguru import logger
from config import WHALE_SCORING


def calc_whale_score(symbol: str, db) -> float:
    """
    Hitung whale accumulation/distribution score.
    BTC/ETH: gunakan exchange netflow 7 hari dari tabel onchain.
    Altcoin: gunakan OI anomaly dari tabel futures_metrics.
    Return: -10 to +8.
    """
    cfg = WHALE_SCORING

    if symbol == "BTCUSDT":
        netflow = db.get_whale_netflow_7d("BTCUSDT")
        if netflow is None:
            return 0.0
        if netflow < cfg["btc_outflow_bullish"]:   # heavy outflow = accumulation
            return 8.0
        if netflow < 0:
            return 4.0
        if netflow > cfg["btc_inflow_bearish"]:    # heavy inflow = distribution
            return -10.0
        if netflow > 0:
            return -4.0
        return 0.0

    if symbol == "ETHUSDT":
        netflow = db.get_whale_netflow_7d("ETHUSDT")
        if netflow is None:
            return 0.0
        if netflow < cfg["eth_outflow_bullish"]:
            return 8.0
        if netflow < 0:
            return 4.0
        if netflow > cfg["eth_inflow_bearish"]:
            return -10.0
        if netflow > 0:
            return -4.0
        return 0.0

    # Altcoins: OI anomaly detection
    oi_data = db.get_oi_change_7d(symbol)
    if not oi_data:
        return 0.0

    oi_chg  = oi_data["oi_change_pct"]
    funding = oi_data["avg_funding"]

    # OI naik besar + funding netral = smart money masuk diam-diam (bullish)
    if oi_chg >= cfg["oi_surge_bullish"] and abs(funding) <= cfg["funding_neutral_max"]:
        return 5.0

    # OI turun besar = smart money keluar (bearish)
    if oi_chg <= cfg["oi_drop_bearish"]:
        return -5.0

    return 0.0


def get_whale_modifier(symbol: str, db) -> float:
    """Return whale score modifier untuk engine.py."""
    try:
        return calc_whale_score(symbol, db)
    except Exception as e:
        logger.error(f"Whale modifier error for {symbol}: {e}")
        return 0.0
```

- [ ] **Step 4: Run — 8 passed:**
```
py -m pytest tests/test_phase5.py -v
```

- [ ] **Step 5: Full suite — 73 passed:**
```
py -m pytest tests/ --tb=short 2>&1 | tail -5
```

- [ ] **Step 6: Commit:**
```
git add collector/whale.py tests/test_phase5.py
git commit -m "feat: add whale accumulation proxy — netflow BTC/ETH + OI anomaly altcoin"
```

---

## Task 4: Engine Integration + main.py CLI

**Files:** `signals/engine.py`, `main.py`

- [ ] **Step 1: Update signals/engine.py**

Tambah 2 import (setelah existing Phase 4 imports):
```python
from collector.social import get_social_modifier
from collector.whale import get_whale_modifier
```

Cari blok Phase 4 di score_coin() — setelah `total = score_clamp(total + news_mod + options_mod)`, tambahkan:
```python
    # Phase 5: Social + Whale modifiers
    social_mod = get_social_modifier(symbol, db)
    whale_mod  = get_whale_modifier(symbol, db)
    total      = score_clamp(total + social_mod + whale_mod)
```

Tambahkan 2 key baru ke result dict:
```python
        "social_modifier":  social_mod,
        "whale_modifier":   whale_mod,
```

- [ ] **Step 2: Update main.py — tambah social ke --collect-onchain --full**

Di `elif args.collect_onchain:` block, tambahkan setelah `collect_all_token_unlocks()`:
```python
            from collector.social import collect_all_social
            logger.info("Collecting social metrics (CoinGecko)...")
            collect_all_social(db)
```

- [ ] **Step 3: Full test suite — semua pass:**
```
py -m pytest tests/ -v --tb=short 2>&1 | tail -10
```
Expected: 73 passed

- [ ] **Step 4: Verifikasi imports:**
```
py -c "
from collector.social import get_social_modifier, collect_all_social
from collector.whale import get_whale_modifier, calc_whale_score
from signals.engine import score_coin
print('Phase 5 imports OK')
"
```

- [ ] **Step 5: Commit:**
```
git add signals/engine.py main.py
git commit -m "feat: Phase 5 complete — social sentiment + whale proxy integrated into engine"
```
