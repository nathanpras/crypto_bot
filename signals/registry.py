# signals/registry.py
from datetime import datetime

SIGNAL_REGISTRY = [
    # ── TECHNICAL (10 signals) ──────────────────────────────────
    {"id": "T1",  "name": "trend_alignment",     "category": "TECHNICAL",   "update_freq": "4H", "source": "bybit_candles",    "enabled": True},
    {"id": "T2",  "name": "rsi_momentum",        "category": "TECHNICAL",   "update_freq": "4H", "source": "bybit_candles",    "enabled": True},
    {"id": "T3",  "name": "macd_momentum",       "category": "TECHNICAL",   "update_freq": "4H", "source": "bybit_candles",    "enabled": True},
    {"id": "T4",  "name": "volume_confirm",      "category": "TECHNICAL",   "update_freq": "4H", "source": "bybit_candles",    "enabled": True},
    {"id": "T5",  "name": "wyckoff_phase",       "category": "TECHNICAL",   "update_freq": "4H", "source": "bybit_candles",    "enabled": True},
    {"id": "T6",  "name": "vwap_deviation",      "category": "TECHNICAL",   "update_freq": "4H", "source": "bybit_candles",    "enabled": True},
    {"id": "T7",  "name": "volume_delta",        "category": "TECHNICAL",   "update_freq": "4H", "source": "bybit_candles",    "enabled": True},
    {"id": "T8",  "name": "bb_squeeze",          "category": "TECHNICAL",   "update_freq": "4H", "source": "bybit_candles",    "enabled": True},
    {"id": "T9",  "name": "orderbook_imbalance", "category": "TECHNICAL",   "update_freq": "4H", "source": "bybit_orderbook",  "enabled": True},
    {"id": "T10", "name": "funding_oscillator",  "category": "TECHNICAL",   "update_freq": "6H", "source": "coinalyze",        "enabled": True},
    # ── ON-CHAIN (7 signals) ────────────────────────────────────
    {"id": "O1",  "name": "mvrv_ratio",          "category": "ON_CHAIN",    "update_freq": "1D", "source": "coinmetrics",      "enabled": True},
    {"id": "O2",  "name": "exchange_netflow",    "category": "ON_CHAIN",    "update_freq": "1D", "source": "coinmetrics",      "enabled": True},
    {"id": "O3",  "name": "btc_real_onchain",    "category": "ON_CHAIN",    "update_freq": "1D", "source": "blockchain_info",  "enabled": True},
    {"id": "O4",  "name": "eth_real_onchain",    "category": "ON_CHAIN",    "update_freq": "1D", "source": "etherscan",        "enabled": True},
    {"id": "O5",  "name": "liquidation_cascade", "category": "ON_CHAIN",    "update_freq": "1H", "source": "coinglass",        "enabled": True},
    {"id": "O6",  "name": "nvt_signal",          "category": "ON_CHAIN",    "update_freq": "1D", "source": "blockchain_info",  "enabled": True},
    {"id": "O7",  "name": "perp_spot_ratio",     "category": "ON_CHAIN",    "update_freq": "4H", "source": "bybit_coingecko",  "enabled": True},
    # ── SENTIMENT (6 signals) ───────────────────────────────────
    {"id": "S1",  "name": "fear_greed",          "category": "SENTIMENT",   "update_freq": "1H", "source": "alternative_me",  "enabled": True},
    {"id": "S2",  "name": "news_sentiment",      "category": "SENTIMENT",   "update_freq": "1H", "source": "rss_vader",        "enabled": True},
    {"id": "S3",  "name": "social_coingecko",    "category": "SENTIMENT",   "update_freq": "1D", "source": "coingecko",        "enabled": True},
    {"id": "S4",  "name": "lunarcrush_galaxy",   "category": "SENTIMENT",   "update_freq": "1H", "source": "lunarcrush",       "enabled": True},
    {"id": "S5",  "name": "google_trends",       "category": "SENTIMENT",   "update_freq": "1D", "source": "pytrends",         "enabled": True},
    {"id": "S6",  "name": "reddit_sentiment",    "category": "SENTIMENT",   "update_freq": "1D", "source": "reddit_praw",      "enabled": True},
    # ── DERIVATIVES (4 signals) ─────────────────────────────────
    {"id": "D1",  "name": "oi_funding",          "category": "DERIVATIVES", "update_freq": "1H", "source": "bybit_futures",    "enabled": True},
    {"id": "D2",  "name": "long_short_ratio",    "category": "DERIVATIVES", "update_freq": "1H", "source": "bybit_futures",    "enabled": True},
    {"id": "D3",  "name": "options_pcr",         "category": "DERIVATIVES", "update_freq": "1H", "source": "deribit",          "enabled": True},
    {"id": "D4",  "name": "futures_basis",       "category": "DERIVATIVES", "update_freq": "6H", "source": "bybit_basis",      "enabled": True},
    # ── MACRO (5 signals) ───────────────────────────────────────
    {"id": "M1",  "name": "stablecoin_flows",    "category": "MACRO",       "update_freq": "6H", "source": "defillama",        "enabled": True},
    {"id": "M2",  "name": "tvl_narrative",       "category": "MACRO",       "update_freq": "1D", "source": "defillama",        "enabled": True},
    {"id": "M3",  "name": "altseason_index",     "category": "MACRO",       "update_freq": "4H", "source": "bybit_prices",     "enabled": True},
    {"id": "M4",  "name": "dex_cex_ratio",       "category": "MACRO",       "update_freq": "1D", "source": "defillama_dex",    "enabled": True},
    {"id": "M5",  "name": "global_macro",        "category": "MACRO",       "update_freq": "1D", "source": "fred_alternative", "enabled": True},
]

SIGNAL_ID_INDEX = {s["id"]: s for s in SIGNAL_REGISTRY}


def get_signal_ids() -> list:
    return [s["id"] for s in SIGNAL_REGISTRY]


def populate_registry_to_db(db) -> None:
    """Write registry metadata to signal_registry table. Safe to call repeatedly."""
    ts = datetime.utcnow()
    for sig in SIGNAL_REGISTRY:
        db.conn.execute("""
            INSERT OR REPLACE INTO signal_registry
                (signal_name, category, update_freq, source, enabled, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [sig["id"], sig["category"], sig["update_freq"],
              sig["source"], sig["enabled"], ts])
