# ============================================================
# config.py — APEX Trading System Configuration
# ============================================================

# ─── Asset Universe ───────────────────────────────────────
COINS = {
    # TIER 1 — Always active, both engines
    "BTCUSDT": {"tier": 1, "name": "Bitcoin",       "min_vol_usd": 500_000_000},
    "ETHUSDT": {"tier": 1, "name": "Ethereum",      "min_vol_usd": 200_000_000},

    # TIER 2 — Active when BTC.D falling
    "SOLUSDT":  {"tier": 2, "name": "Solana",       "min_vol_usd": 100_000_000},
    "XRPUSDT":  {"tier": 2, "name": "Ripple",       "min_vol_usd": 80_000_000},
    "BNBUSDT":  {"tier": 2, "name": "BNB",          "min_vol_usd": 50_000_000},
    "ADAUSDT":  {"tier": 2, "name": "Cardano",      "min_vol_usd": 30_000_000},
    "AVAXUSDT": {"tier": 2, "name": "Avalanche",    "min_vol_usd": 25_000_000},
    "LINKUSDT": {"tier": 2, "name": "Chainlink",    "min_vol_usd": 25_000_000},
    "DOTUSDT":  {"tier": 2, "name": "Polkadot",     "min_vol_usd": 20_000_000},
    "TONUSDT":  {"tier": 2, "name": "Toncoin",      "min_vol_usd": 20_000_000},

    # TIER 3 — Active only in confirmed altseason
    "ONDOUSDT": {"tier": 3, "name": "Ondo Finance", "min_vol_usd": 15_000_000},
    "ARBUSDT":  {"tier": 3, "name": "Arbitrum",     "min_vol_usd": 10_000_000},
    "OPUSDT":   {"tier": 3, "name": "Optimism",     "min_vol_usd": 10_000_000},
    "NEARUSDT": {"tier": 3, "name": "NEAR Protocol","min_vol_usd": 10_000_000},
    "INJUSDT":  {"tier": 3, "name": "Injective",    "min_vol_usd": 10_000_000},
    "SUIUSDT":  {"tier": 3, "name": "Sui",          "min_vol_usd": 15_000_000},
    "APTUSDT":  {"tier": 3, "name": "Aptos",        "min_vol_usd": 10_000_000},
    "SEIUSDT":  {"tier": 3, "name": "Sei",          "min_vol_usd": 5_000_000},
    "POLUSDT":  {"tier": 3, "name": "Polygon",      "min_vol_usd": 8_000_000},
}

# ─── Timeframes ───────────────────────────────────────────
TIMEFRAMES = {
    "primary":   "4h",   # Signal engine + entry decisions
    "structure": "1d",   # Market structure + regime
    "macro":     "1w",   # Macro trend (weekly candles)
    "entry":     "1h",   # Precision entry timing
}

HISTORICAL_PERIODS = {
    "4h": 730,   # 2 years of 4H candles (2 × 365 × 6 candles/day)
    "1d": 730,   # 2 years of daily candles
    "1w": 156,   # 3 years of weekly candles
    "1h": 180,   # 30 days of 1H candles (enough for entry)
}

# ─── Signal Engine ────────────────────────────────────────
SIGNAL_WEIGHTS = {
    "trend_alignment":    0.20,  # Multi-timeframe trend direction
    "rsi_momentum":       0.15,  # RSI 4H (oversold/overbought + divergence)
    "macd_momentum":      0.10,  # MACD histogram direction
    "volume_confirm":     0.15,  # Volume vs 20-period average
    "wyckoff_phase":      0.15,  # Wyckoff accumulation pattern score
    "onchain_signal":     0.15,  # On-chain smart money score
    "sentiment_score":    0.10,  # Fear & Greed + funding rates
}

SIGNAL_THRESHOLD = 70       # Minimum score to consider a trade
SIGNAL_STRONG    = 82       # "Perfect Storm" threshold → 3× size

# ─── Risk Management ──────────────────────────────────────
RISK_PER_TRADE = {
    1: 0.020,   # Tier 1: 2.0% portfolio risk
    2: 0.015,   # Tier 2: 1.5% portfolio risk
    3: 0.010,   # Tier 3: 1.0% portfolio risk
}
PERFECT_STORM_MULTIPLIER = 3.0   # Risk multiplier when score ≥ SIGNAL_STRONG
MAX_OPEN_POSITIONS       = 3
MAX_DAILY_LOSS_PCT       = 0.03  # Halt trading if -3% day
MAX_DRAWDOWN_PCT         = 0.15  # Full review if -15% from peak

STOP_LOSS_PCT = {
    1: 0.08,    # BTC/ETH: 8% stop
    2: 0.10,    # Large alts: 10% stop
    3: 0.12,    # Mid alts: 12% stop
}
MIN_RR_RATIO = 5.0              # Minimum 5:1 reward/risk

# ─── Portfolio Allocation ─────────────────────────────────
PORTFOLIO_ALLOCATION = {
    "core":          0.70,   # BTC + ETH — never traded
    "category_kings": 0.20,  # 2-3 Category King coins
    "moonshots":     0.10,   # 1-2 early narrative coins
}
CORE_SPLIT = {"BTCUSDT": 0.714, "ETHUSDT": 0.286}  # 50% / 20% of total

# ─── Filter Thresholds ────────────────────────────────────
FILTERS = {
    # F2: Cycle Gate
    "btc_dominance_altseason": 56.0,  # BTC.D below this = altseason
    "btc_dominance_check":     62.0,  # BTC.D above this = BTC only

    # F4: Accumulation Gate
    "exchange_outflow_weeks": 2,      # Weeks of consecutive outflow needed
    "whale_accumulation_pct": 0.5,    # % increase in whale holdings

    # F5: Entry Gate
    "wyckoff_min_score":   60,        # Minimum Wyckoff pattern confidence
    "volume_spring_max":   0.70,      # Spring volume must be < 70% of avg
    "volume_breakout_min": 1.80,      # Breakout volume must be > 180% of avg

    # Regime (ADX-based)
    "adx_trending":  25,              # ADX > 25 = trending
    "adx_ranging":   18,              # ADX < 18 = ranging/sideways
}

# ─── Exchange Settings ────────────────────────────────────
EXCHANGE = "binance"
EXCHANGE_FEE = 0.001     # 0.1% per side (0.075% with BNB)
ORDER_TYPE = "limit"     # Never market orders (except emergency)
TP1_PORTION  = 0.50      # Take 50% profit at TP1
TP2_TRAIL    = True      # Trail remaining 50% with ATR stop
ATR_TRAIL_MULT = 2.0     # 2× ATR for trailing stop

# ─── Timing ───────────────────────────────────────────────
KILL_ZONES_UTC = [
    (7, 0,  10, 0),   # London open
    (13, 0, 16, 0),   # New York open
]
NEWS_BLACKOUT_MINUTES = 30   # No trades 30 min before/after major news

# ─── Data Sources (all free) ──────────────────────────────
DATA_SOURCES = {
    "ohlcv":       "binance_api",
    "funding":     "binance_api",
    "fear_greed":  "https://api.alternative.me/fng/",
    "onchain_btc": "https://community-api.coinmetrics.io/v4",
    "onchain_eth": "https://api.etherscan.io/api",
    "macro_m2":    "https://api.stlouisfed.org/fred/series/observations",
    "news":        "https://cryptopanic.com/api/v1/posts/",
}

# ─── Telegram Messages ────────────────────────────────────
TELEGRAM_SIGNALS = True
TELEGRAM_DAILY_REPORT = True
TELEGRAM_DAILY_REPORT_TIME = "00:05"  # UTC

# ─── Logging ──────────────────────────────────────────────
LOG_FILE = "logs/apex.log"
LOG_ROTATION = "1 week"
