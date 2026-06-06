# Phase 8: Maximum Signal Architecture
**Date:** 2026-06-05  
**Status:** Approved  
**Goal:** Maximize APEX signal accuracy before Oracle Cloud production deployment, using all available free-tier data sources and a regime-aware weight optimization architecture.

---

## 1. Problem Statement

Current system (post Phase 7) has:
- 18 signals across 7 weighted + many loose additive modifiers
- Single global Optuna weight optimization (ignores regime context)
- Critical data quality gaps: liquidations are proxy-only, social is follower-count only, exchange netflows are estimated
- No signal registry → hard to add/remove/audit signals
- Modifiers can double-count or cancel arbitrarily

Target: 32 registered signals, each normalized to 0-100, with Optuna-optimized weights **per market regime** (5 sets × 32 signals = 160 parameters). Walk-forward validation prevents overfitting.

---

## 2. Architecture

```
[Data Collectors] → [Signal Normalizer] → [Regime Detector]
                                               ↓
                         [Regime-Aware Weights from DB]
                                               ↓
                         [Weighted Sum → score 0-100]
                                               ↓
                         [Hard Gates: news block, unlock penalty]
                                               ↓
                                          SIGNAL FIRED
```

All signals enter through a **Signal Registry** — a central manifest with metadata per signal (name, category, update frequency, data source, enabled flag). The engine reads the registry at startup and applies the latest optimized weights from DuckDB.

---

## 3. Signal Registry (32 Signals Total)

### Category A: TECHNICAL (update per 4H candle, 10 signals)
| ID | Signal | Source |
|----|--------|--------|
| T1 | Trend Alignment (EMA 20/50/200 multi-TF) | Bybit candles |
| T2 | RSI 14 + bullish divergence | Bybit candles |
| T3 | MACD histogram direction + cross | Bybit candles |
| T4 | Volume confirmation (up/down candle vol) | Bybit candles |
| T5 | Wyckoff phase (Spring/LPS/SOS) | Bybit candles |
| T6 | VWAP deviation | Bybit candles |
| T7 | Volume delta (10-candle buy/sell pressure) | Bybit candles |
| T8 | Bollinger Band squeeze | Bybit candles |
| T9 | **[NEW]** Order book imbalance (L2 bid/ask depth) | Bybit orderbook API |
| T10 | **[NEW]** Funding rate oscillator (funding vs 30d MA) | Bybit + Coinalyze history |

### Category B: ON-CHAIN (update per hour/day, 7 signals)
| ID | Signal | Source |
|----|--------|--------|
| O1 | MVRV ratio | CoinMetrics community |
| O2 | Exchange netflow estimated | CoinMetrics community |
| O3 | **[NEW]** BTC real netflow + active addresses | Blockchain.com (free, no key) |
| O4 | **[NEW]** ETH real netflow + active addresses | Etherscan (free key) |
| O5 | **[NEW]** Real liquidation cascade (long/short) | CoinGlass (free key) |
| O6 | **[NEW]** NVT signal (network value / tx volume) | Computed from Blockchain.com |
| O7 | **[NEW]** Perp volume / spot volume ratio | Bybit + CoinGecko |

### Category C: SENTIMENT & SOCIAL (update per hour, 6 signals)
| ID | Signal | Source |
|----|--------|--------|
| S1 | Fear & Greed index | Alternative.me (free) |
| S2 | News sentiment VADER (24h rolling) | 14 RSS feeds + VADER |
| S3 | Social score (CoinGecko followers/GitHub) | CoinGecko (free) |
| S4 | **[NEW]** LunarCrush Galaxy Score | LunarCrush (free key) |
| S5 | **[NEW]** Google Trends search interest | pytrends (free, no key) |
| S6 | **[NEW]** Reddit post sentiment (r/CryptoCurrency etc.) | Reddit PRAW (free key) |

### Category D: DERIVATIVES (update per hour, 4 signals)
| ID | Signal | Source |
|----|--------|--------|
| D1 | OI change 24h + funding rate | Bybit |
| D2 | Long/Short ratio (contrarian) | Bybit |
| D3 | Options put/call ratio + IV skew (BTC/ETH) | Deribit (free) |
| D4 | Futures basis (spot vs futures premium) | Bybit |

### Category E: MACRO & ECOSYSTEM (update daily, 5 signals)
| ID | Signal | Source |
|----|--------|--------|
| M1 | Stablecoin supply 7d change | DeFiLlama (free) |
| M2 | TVL narrative (sector rotation) | DeFiLlama (free) |
| M3 | **[NEW]** Altcoin season index | Computed from prices |
| M4 | **[NEW]** DEX/CEX volume ratio | DeFiLlama DEX (free) |
| M5 | Global M2 + CPI + 10Y yield | FRED (free key, optional) |

---

## 4. Regime-Aware Weight Optimization

### Weight Structure
```python
OPTIMIZED_WEIGHTS = {
    "TRENDING_BULL":  {"T1": 0.08, "T2": 0.05, ..., "M5": 0.02},
    "TRENDING_BEAR":  {...},
    "RANGING":        {...},
    "VOLATILE":       {...},
    "TRANSITIONING":  {...},
}
# Constraints: sum(weights) == 1.0 per regime
#              0.0 <= each weight <= 0.15 (no signal dominates)
```

### Optuna Fitness Function (upgraded)
```python
fitness = (
    0.40 * sharpe_ratio        # risk-adjusted return
  + 0.25 * win_rate            # % signals that were profitable
  + 0.20 * (1 - max_drawdown)  # capital preservation
  + 0.15 * profit_factor       # gross_profit / gross_loss
)
```

### Walk-Forward Validation
- Train window: 9 months rolling
- Validation window: 3 months
- Slide: 1 month per iteration
- Minimum windows for acceptance: 6
- Accept weights only if consistent (std dev of fitness < 0.15 across windows)

### Weight Storage
Weights stored in DuckDB `optimized_weights` table. Engine loads latest per regime at startup. Re-optimization runs every Monday 01:00 UTC automatically.

---

## 5. New Data Sources: API Specs

### Blockchain.com (BTC, free, no key)
- `GET https://api.blockchain.info/stats` → active addresses, tx count, hash rate
- `GET https://api.blockchain.info/charts/estimated-transaction-volume-usd?timespan=5days&format=json` → transaction volume
- Update: daily

### Etherscan (ETH, free API key)
- `GET https://api.etherscan.io/api?module=stats&action=dailytx` → daily transaction count
- `GET https://api.etherscan.io/api?module=stats&action=dailyavgblocktime` → network activity proxy
- Active addresses approximated from unique sender count in daily tx data
- Update: daily

### CoinGlass (free API key, 10 req/min)
- `GET https://open-api.coinglass.com/api/pro/v3/futures/liquidation-history`
- Returns: long_liq_usd, short_liq_usd per symbol per hour
- Replaces current proxy liquidation calculation entirely
- Update: hourly

### LunarCrush (free key, 10 req/min)
- `GET https://lunarcrush.com/api4/public/coins/{coin}/v1`
- Returns: galaxy_score (1-100), alt_rank, social_volume, social_score
- Update: hourly

### pytrends / Google Trends (free, no key)
- `TrendReq().build_payload([kw], timeframe='now 7-d')`
- Returns: interest score 0-100 for search terms like "bitcoin", "ethereum buy"
- Update: daily (rate limited, ~1 req/15 sec)

### Reddit PRAW (free key)
- Subreddits: r/CryptoCurrency, r/Bitcoin, r/ethereum, r/altcoin
- Fetch top 50 posts per day → VADER sentiment → rolling 24h avg
- Update: daily

### Coinalyze (free key, 100 req/day)
- `GET https://api.coinalyze.net/v1/funding-rate-history`
- Returns: 30-day funding rate history per symbol
- Used for: funding rate oscillator (current - 30d MA)
- Update: hourly

### Bybit L2 Orderbook (existing connection, no key)
- `GET /v5/market/orderbook?category=linear&symbol={symbol}&limit=50`
- Compute: bid_volume_top10 / ask_volume_top10 = order book imbalance ratio
- Update: per 4H candle

---

## 6. Database Schema Changes

### New Tables
```sql
CREATE TABLE signal_registry (
    signal_name    VARCHAR PRIMARY KEY,
    category       VARCHAR,
    update_freq    VARCHAR,
    source         VARCHAR,
    enabled        BOOLEAN DEFAULT TRUE,
    last_updated   TIMESTAMP
);

CREATE TABLE optimized_weights (
    regime         VARCHAR,
    signal_name    VARCHAR,
    weight         DOUBLE,
    fitness_score  DOUBLE,
    optimized_at   TIMESTAMP,
    PRIMARY KEY (regime, signal_name, optimized_at)
);

CREATE TABLE liquidations (
    symbol         VARCHAR,
    timestamp      TIMESTAMP,
    liq_long_usd   DOUBLE,
    liq_short_usd  DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE lunarcrush_metrics (
    symbol         VARCHAR,
    timestamp      TIMESTAMP,
    galaxy_score   DOUBLE,
    alt_rank       INTEGER,
    social_volume  DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE google_trends (
    symbol         VARCHAR,
    date           DATE,
    interest       INTEGER,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE onchain_real (
    asset          VARCHAR,
    date           DATE,
    active_addr    BIGINT,
    tx_count       BIGINT,
    exchange_inflow  DOUBLE,
    exchange_outflow DOUBLE,
    nvt_ratio      DOUBLE,
    PRIMARY KEY (asset, date)
);

CREATE TABLE reddit_sentiment (
    symbol         VARCHAR,
    date           DATE,
    post_count     INTEGER,
    avg_sentiment  DOUBLE,
    bullish_pct    DOUBLE,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE funding_history (
    symbol         VARCHAR,
    timestamp      TIMESTAMP,
    funding_rate   DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);
```

---

## 7. Collector Schedule (Oracle Cloud Cron)

```
*/240 min  → collect_candles, collect_onchain_futures, collect_orderbook
*/60 min   → collect_liquidations (CoinGlass)
*/60 min   → collect_lunarcrush
*/360 min  → collect_stablecoin, collect_basis, collect_fees
00:00 UTC  → collect_blockchain_btc, collect_etherscan
00:05 UTC  → collect_google_trends
00:10 UTC  → collect_reddit_sentiment
00:20 UTC  → collect_coinmetrics (full MVRV)
00:30 UTC  → collect_token_unlocks
01:00 UTC  → collect_deribit_options
Mon 01:00  → optimize_weights (all regimes, 300 trials each)
```

---

## 8. Signal Staleness Policy

| Category | Max age before signal returns 50.0 (neutral) |
|----------|----------------------------------------------|
| Candles (T1-T8) | 4H — must be fresh |
| Orderbook (T9) | 4H |
| Funding oscillator (T10) | 6H |
| On-chain real (O3, O4) | 48H |
| Liquidations (O5) | 6H |
| NVT (O6) | 48H |
| LunarCrush (S4) | 24H |
| Google Trends (S5) | 7 days |
| Reddit (S6) | 48H |
| Macro (M1-M5) | 7 days |

---

## 9. Paid Alternatives (Optional Upgrades)

| Provider | Cost/month | Key additions | Est. win rate improvement |
|----------|-----------|---------------|--------------------------|
| Glassnode Starter | $29 | SOPR, entity-adjusted flows, realized cap, miner outflows | +8-12% |
| CryptoQuant Basic | $49 | Exchange reserve by exchange, stablecoin supply per exchange, miner-to-exchange | +6-10% |
| Santiment Basic | $49 | Dev activity real-time, social volume anomaly, whale tx alerts | +5-8% |
| Twitter/X API Basic | $100 | Real-time tweet monitoring, influencer tracking | +3-5% |
| Glassnode Advanced | $99 | RHODL ratio, LTH/STH metrics, exchange dominance | +12-18% |
| Kaiko | $500+ | Institutional L2 data, cross-exchange arbitrage | +10-15% |

**Recommended upgrade path if profitable:** Glassnode Starter ($29) → CryptoQuant Basic ($49) = $78/month total, estimated +14-22% win rate improvement.

---

## 10. Environment Variables Required

```env
# Free, requires registration
ETHERSCAN_API_KEY=
COINGLASS_API_KEY=
LUNARCRUSH_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=APEX/1.0 by YourRedditUsername
COINALYZE_API_KEY=
FRED_API_KEY=           # optional, already in use

# Already configured
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
PAPER_TRADING=true
```

---

## 11. Implementation Phases

| Phase | What | Files |
|-------|------|-------|
| 8A | New collectors (CoinGlass, Blockchain.com, Etherscan, LunarCrush, pytrends, Reddit, Coinalyze, Orderbook) | `collector/liquidations.py`, `collector/onchain_real.py`, `collector/social_lunar.py`, `collector/orderbook.py` |
| 8B | Signal Registry + universal normalizer (all 32 → 0-100) | `signals/registry.py`, `signals/normalizer.py` |
| 8C | DB schema migration (7 new tables) | `database.py` |
| 8D | Regime-aware weight storage + engine refactor | `signals/engine.py`, `backtesting/optimizer.py` |
| 8E | Optuna fitness function upgrade + walk-forward | `backtesting/optimizer.py`, `backtesting/walk_forward.py` |
| 8F | Oracle Cloud deployment config (systemd + cron) | `deploy/`, `.env.example`, `deploy/apex.service` |
| 8G | Full test suite + backtest validation | `tests/test_phase8*.py` |

---

## 12. Success Criteria

- All 32 signals producing values (not 50.0 default) on first run
- `--backtest` shows Sharpe ≥ 1.5, win rate ≥ 60% on walk-forward validation
- `--optimize-weights` completes < 3 hours on Oracle Cloud
- All collectors resilient to API failures (never crash engine)
- 148 existing tests still passing + ≥ 60 new tests added
