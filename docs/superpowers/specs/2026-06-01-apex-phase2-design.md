# APEX Trading System — Phase 2 Design Spec
**Date:** 2026-06-01
**Status:** Approved
**Approach:** Signal Intelligence Rebuild (Pendekatan C)

---

## 1. Konteks & Tujuan

Phase 1 selesai dengan infrastruktur lengkap: 19 coin, DuckDB, WebSocket streaming, 7-signal scoring engine, F1+F2 gates, dan Telegram alerts. Eksekusi order tetap **manual** oleh user.

**Tujuan Phase 2:** Maksimalkan akurasi sinyal dan win rate dengan:
1. Memperbaiki kelemahan terbesar Phase 1 (on-chain stub 50/100 untuk 17/19 coin)
2. Menambah konteks sector rotation dan token unlock
3. Mengoptimasi bobot sinyal berdasarkan data historis 2 tahun yang sudah ada

---

## 2. Arsitektur

Phase 2 duduk **di atas** Phase 1 — tidak mengganti file yang sudah ada, hanya menambah dan memodifikasi di satu titik.

### File Baru
```
collector/
  onchain_enhanced.py   — CoinMetrics (BTC/ETH) + Binance Futures (semua coin)
  narrative.py          — DeFiLlama TVL sector rotation
  token_unlocks.py      — Tokenomist.ai scraper

backtesting/
  __init__.py
  harness.py            — VectorBT replay sinyal di data historis
  optimizer.py          — Optuna weight search (300 trials)
  walk_forward.py       — 18-bulan train + 6-bulan validation split
```

### File Dimodifikasi
```
database.py    — +4 tabel baru
engine.py      — +3 baris modifier setelah total_score dihitung
manager.py     — format_trade_for_telegram() diperluas dengan context section
main.py        — +3 CLI command baru
config.py      — +SECTOR_MAP, +FUTURES_WEIGHTS, +UNLOCK_PENALTIES
setup.sh       — +dependensi baru (vectorbt, optuna, beautifulsoup4, playwright)
```

---

## 3. Database — 4 Tabel Baru

### 3.1 `futures_metrics`
Data Binance Futures per coin, update setiap 6 jam.
```sql
CREATE TABLE futures_metrics (
    symbol              VARCHAR NOT NULL,
    timestamp           TIMESTAMP NOT NULL,
    open_interest       DOUBLE,      -- USD value
    oi_change_24h_pct   DOUBLE,      -- % change in 24h
    funding_rate        DOUBLE,      -- current funding rate
    long_short_ratio    DOUBLE,      -- long/short account ratio
    liq_long_24h        DOUBLE,      -- long liquidations USD (24h)
    liq_short_24h       DOUBLE,      -- short liquidations USD (24h)
    PRIMARY KEY (symbol, timestamp)
);
```

### 3.2 `sector_tvl`
DeFiLlama TVL per sektor, update setiap 24 jam.
```sql
CREATE TABLE sector_tvl (
    sector          VARCHAR NOT NULL,
    date            DATE NOT NULL,
    tvl_usd         DOUBLE,
    tvl_change_7d   DOUBLE,   -- % change
    tvl_change_30d  DOUBLE,   -- % change
    PRIMARY KEY (sector, date)
);
```

### 3.3 `token_unlocks`
Jadwal token unlock dari Tokenomist.ai, update setiap 24 jam.
```sql
CREATE TABLE token_unlocks (
    symbol              VARCHAR NOT NULL,
    unlock_date         DATE NOT NULL,
    unlock_amount_usd   DOUBLE,
    unlock_pct_supply   DOUBLE,   -- % of circulating supply
    category            VARCHAR,  -- "team", "investor", "ecosystem", dll
    PRIMARY KEY (symbol, unlock_date)
);
```

### 3.4 `backtest_results`
Hasil setiap run backtest dan optimasi.
```sql
CREATE TABLE backtest_results (
    run_id          VARCHAR PRIMARY KEY,
    run_date        TIMESTAMP,
    weights_json    VARCHAR,      -- JSON string of signal weights
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
    deployed        BOOLEAN DEFAULT FALSE  -- apakah weights ini aktif
);
```

---

## 4. Komponen Sinyal

### 4.1 On-Chain Enhanced (`collector/onchain_enhanced.py`)

**Jalur BTC / ETH** — tetap pakai CoinMetrics community API (gratis, tanpa key):
- MVRV ratio
- Exchange netflow (7-day rolling average)
- NUPL (Net Unrealized Profit/Loss)

**Jalur 17 Altcoin** — Binance Futures API (gratis, sudah terhubung):

| Sinyal | Bullish | Bearish |
|--------|---------|---------|
| OI momentum | OI naik + harga naik | OI naik + harga turun (divergence) |
| Funding rate | < -0.01% (shorts paying) | > 0.05% (longs overleveraged) |
| Long/Short ratio | < 0.8 (ekstrem short) = squeeze setup | > 2.0 (longs dominan) = crowded |
| Liquidation cascade | Long liq besar dalam 24h = potential bottom | Short liq besar = blow-off top |

**Scoring logic altcoin:**
```
OI naik + funding negatif + harga naik  → 85
OI naik + funding positif rendah        → 65
Funding > 0.05% (overleveraged)         → 20
Long liquidation besar 24h              → 80
Netral / data tidak cukup               → 50
```

**Frekuensi fetch:** Setiap 6 jam via cron.

---

### 4.2 F3 Narrative Gate → Score Modifier (`collector/narrative.py`)

DeFiLlama TVL API — gratis, tidak butuh API key.

**Coin → Sektor mapping** (disimpan di `config.py` sebagai `SECTOR_MAP`):
```python
SECTOR_MAP = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",  # L1
    "BNBUSDT": "binance-smart-chain",
    "AVAXUSDT": "avalanche",
    "ARBUSDT": "arbitrum",   # L2
    "OPUSDT":  "optimism",
    "POLUSDT": "polygon",
    "ONDOUSDT": "rwa",       # RWA/DeFi
    "LINKUSDT": "oracle",
    "INJUSDT":  "defi",
    "DOTUSDT":  "polkadot",
    "NEARUSDT": "near",
    "SUIUSDT":  "sui",
    "APTUSDT":  "aptos",
    "SEIUSDT":  "sei",
    "TONUSDT":  "ton",
    "XRPUSDT":  "payments",
    "ADAUSDT":  "cardano",
}
```

**Score modifier berdasarkan TVL 30d change:**
```python
NARRATIVE_MODIFIERS = {
    "strong_up":   (+5,  "+20% TVL"),    # TVL change_30d > +20%
    "mild_up":     (+2,  "+10% TVL"),    # TVL change_30d > +10%
    "neutral":     (0,   "flat TVL"),    # -10% to +10%
    "mild_down":   (-3,  "-10% TVL"),    # TVL change_30d < -10%
    "strong_down": (-8,  "-20% TVL"),    # TVL change_30d < -20%
}
```

**Frekuensi fetch:** Setiap 24 jam.

---

### 4.3 Token Unlock Penalty (`collector/token_unlocks.py`)

**Sumber data:** Tokenomist.ai — scrape halaman per coin.
**Fallback:** Jika scrape gagal, penalty = 0 (tidak memblokir sinyal).

**Penalty logic:**
```python
UNLOCK_PENALTIES = {
    "days_7":          25,   # unlock dalam 7 hari
    "days_14":         20,   # unlock dalam 14 hari
    "days_30":         10,   # unlock dalam 30 hari
    "large_supply":    10,   # tambahan jika unlock > 5% supply
}
# Maksimum total penalty = 35 (tidak bisa mengubah sinyal positif menjadi negatif
# di bawah threshold 70 jika score awal > 105 — tapi score di-clamp 0-100
# sehingga efektif maksimum pengurangan adalah 35 poin)
```

**Frekuensi scrape:** Setiap 24 jam.

---

### 4.4 Integrasi di `engine.py`

Modifikasi hanya di **satu titik** di `score_coin()`, setelah weighted total dihitung:

```python
# === Phase 1 (tidak berubah) ===
s["onchain_score"]   = calc_onchain_score(symbol, db)
# calc_onchain_score() diupgrade: untuk BTC/ETH baca tabel `onchain` (CoinMetrics),
# untuk 17 altcoin baca tabel `futures_metrics` (Binance Futures OI/funding/liq)
# Data kedua tabel di-populate oleh onchain_enhanced.py yang jalan via cron

total = sum(
    s[f"{key}_score"] * weight
    for key, weight in SIGNAL_WEIGHTS.items()
    if f"{key}_score" in s
)

# === Phase 2: 2 modifier post-score ===
sector_mod  = get_sector_modifier(symbol, db)   # F3 narrative: -8 s/d +5
unlock_pen  = get_unlock_penalty(symbol, db)    # token unlock: 0 s/d -35

total = total + sector_mod - unlock_pen
total = score_clamp(total)  # tetap 0-100
```

---

## 5. Backtesting & Weight Optimization

### 5.1 Harness (`backtesting/harness.py`)

VectorBT replay semua sinyal di data historis DuckDB.

**Asumsi simulasi:**
- Entry: open candle berikutnya setelah sinyal fired (realistic, bukan close same candle)
- TP1: 50% position ditutup di TP1 price
- TP2: trailing stop 2×ATR untuk sisa 50%
- Stop loss: sesuai tier (8%/10%/12%)
- Fee: 0.1% per side (0.2% round trip)
- Slippage: 0.05% (konservatif)
- Max 3 posisi simultan

**Output `--backtest`:**
```
APEX Backtest Report (2023-01-01 → 2024-12-31)
══════════════════════════════════════════════
Total trades  : 147
Win rate      : 68.7%
Avg R-multiple: 2.31R
Sharpe ratio  : 1.84
Max drawdown  : -12.3%
Best trade    : SOLUSDT +847% (6 weeks)
Worst trade   : ARBUSDT -12.0%

Per-coin breakdown:
  BTCUSDT : 23 trades | 78% WR | 1.9R avg
  ETHUSDT : 19 trades | 73% WR | 2.1R avg
  SOLUSDT : 18 trades | 72% WR | 3.4R avg
  ...
```

### 5.2 Walk-Forward Optimizer (`backtesting/optimizer.py` + `walk_forward.py`)

**Split data:**
```
Training   : Jan 2023 – Jun 2024 (18 bulan)
Validation : Jul 2024 – Dec 2024 (6 bulan, tidak pernah disentuh saat training)
```

**Optuna search space:**
```python
# Setiap bobot: 5% - 30%, total harus = 100%
# 300 trials, objective = maximize Sharpe ratio on training set
weights = {
    "trend_alignment":  trial.suggest_float("trend", 0.05, 0.30),
    "rsi_momentum":     trial.suggest_float("rsi",   0.05, 0.25),
    "macd_momentum":    trial.suggest_float("macd",  0.05, 0.20),
    "volume_confirm":   trial.suggest_float("vol",   0.05, 0.25),
    "wyckoff_phase":    trial.suggest_float("wyck",  0.05, 0.25),
    "onchain_signal":   trial.suggest_float("chain", 0.05, 0.25),
    "sentiment_score":  trial.suggest_float("sent",  0.05, 0.20),
}
# Normalize agar total = 1.0
```

**Deployment criteria:**
```
Jika val_sharpe > 0.8 DAN val_win_rate > 55%:
    → Update SIGNAL_WEIGHTS di config.py
    → Simpan hasil ke backtest_results dengan deployed=True
    → Kirim notifikasi Telegram

Jika val_sharpe < 0.5:
    → Flag sebagai overfitting, TIDAK update config.py
    → Log warning ke Telegram
```

---

## 6. Telegram Output Baru

Format diperluas dengan section **Context** yang memberikan informasi penuh untuk keputusan manual:

```
🌪 PERFECT STORM — SOLUSDT 📄 PAPER TRADE
═══════════════════════════════════════════
Score   : 87/100  (+5 sector, -0 unlock)
Regime  : TRENDING_BULL
Tier    : 2

Entry   : $142.3400
Stop    : $128.1060  (-9.3%)
TP1     : $177.9250  (+25.0%)
TP2     : $227.7400  (+60.0%)

R/R     : 2.5:1 → TP1  |  6.0:1 → TP2
Position: $87.50  (Rp 1,557,500)
Risk    : $8.75   (Rp 155,750) | 1.50%

── Signal Breakdown ──────────────────────
Trend     ██████████ 82   (20%)
RSI       ████████░░ 74   (15%)
Volume    █████████░ 88   (15%)
Wyckoff   ████████░░ 79   (15%)
On-Chain  ████████░░ 76   (15%)  ← OI↑ funding-0.02%
MACD      ██████░░░░ 63   (10%)
Sentiment ███████░░░ 71   (10%)

── Context ───────────────────────────────
Sector  : L1 TVL +18.4% (30d)  🟢
Unlock  : Tidak ada dalam 30 hari ✓
OI 24h  : +12.3% (naik, bullish)
Funding : -0.02% (shorts paying) 🟢
BTC.D   : 54.2% → Tier 1+2+3 aktif
F&G     : 38 (Fear)
```

---

## 7. CLI Commands Baru

```bash
# Fetch futures metrics saja (OI, funding, liquidations) — cepat, ~30 detik
python3 main.py --collect-onchain

# Fetch semua: futures + DeFiLlama TVL + token unlocks — lengkap, ~2 menit
python3 main.py --collect-onchain --full

# Jalankan backtest dengan periode custom
python3 main.py --backtest
python3 main.py --backtest --from 2023-01-01 --to 2024-12-31

# Optimize bobot sinyal (300 Optuna trials)
python3 main.py --optimize-weights
python3 main.py --optimize-weights --trials 500
```

---

## 8. Scheduler (Oracle Cloud crontab)

```cron
# On-chain + futures: setiap 6 jam
0 */6 * * *  cd /apex && python3 main.py --collect-onchain

# Token unlock + TVL: setiap 24 jam (jam 1 UTC)
5 1 * * *    cd /apex && python3 main.py --collect-onchain --full

# Weight re-optimization: setiap kuartal
0 2 1 1,4,7,10 *  cd /apex && python3 main.py --optimize-weights --trials 300
```

---

## 9. Dependensi Baru

```bash
pip install vectorbt optuna beautifulsoup4 playwright
playwright install chromium  # untuk JS rendering Tokenomist
```

| Library | Versi minimum | Fungsi |
|---------|--------------|--------|
| vectorbt | 0.26+ | Backtesting engine |
| optuna | 3.0+ | Bayesian weight optimization |
| beautifulsoup4 | 4.12+ | HTML scraping |
| playwright | 1.40+ | JS-rendered page scraping |

---

## 10. Urutan Implementasi

1. **Database schema** — tambah 4 tabel baru ke `database.py`
2. **On-chain enhanced** — `collector/onchain_enhanced.py` (fix gap terbesar)
3. **Narrative gate** — `collector/narrative.py` (DeFiLlama)
4. **Token unlock** — `collector/token_unlocks.py` (Tokenomist scraper)
5. **Engine integration** — modifikasi `engine.py` (3 baris modifier)
6. **Telegram format** — update `manager.py` (format baru dengan context)
7. **CLI commands** — update `main.py` (3 command baru)
8. **Backtesting harness** — `backtesting/harness.py` (VectorBT)
9. **Walk-forward optimizer** — `backtesting/optimizer.py` + `walk_forward.py`
10. **Setup & scheduler** — update `setup.sh` + crontab instructions

---

## 11. Constraint & Batasan

- **Semua API gratis** — tidak ada paid subscription
- **Eksekusi tetap manual** — sistem hanya alert, tidak execute order
- **Tokenomist scraper**: jika scrape gagal, penalty = 0 (graceful degradation)
- **CoinMetrics**: community API, rate limit 10 req/menit — handle dengan sleep
- **Overfitting guard**: bobot baru hanya deploy jika val_sharpe > 0.8
- **Target deployment**: Oracle Cloud Always Free (4 OCPU, 24GB RAM)
