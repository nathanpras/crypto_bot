# ============================================================
# database.py — DuckDB Storage Layer
# ============================================================

import duckdb
import pandas as pd
from datetime import datetime
from loguru import logger
from pathlib import Path


DB_PATH = "data/apex.duckdb"

SCHEMA = """
-- Raw OHLCV candles for every coin + timeframe
CREATE TABLE IF NOT EXISTS candles (
    symbol      VARCHAR NOT NULL,
    timeframe   VARCHAR NOT NULL,
    timestamp   TIMESTAMP NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      DOUBLE,
    PRIMARY KEY (symbol, timeframe, timestamp)
);

-- Calculated indicators (computed from candles)
CREATE TABLE IF NOT EXISTS indicators (
    symbol      VARCHAR NOT NULL,
    timeframe   VARCHAR NOT NULL,
    timestamp   TIMESTAMP NOT NULL,
    -- Trend
    ema_20      DOUBLE,
    ema_50      DOUBLE,
    ema_200     DOUBLE,
    adx         DOUBLE,
    adx_pos     DOUBLE,
    adx_neg     DOUBLE,
    -- Momentum
    rsi_14      DOUBLE,
    macd        DOUBLE,
    macd_signal DOUBLE,
    macd_hist   DOUBLE,
    -- Volatility
    atr_14      DOUBLE,
    bb_upper    DOUBLE,
    bb_lower    DOUBLE,
    bb_width    DOUBLE,
    -- Volume
    vol_sma_20  DOUBLE,
    vol_ratio   DOUBLE,
    PRIMARY KEY (symbol, timeframe, timestamp)
);

-- Signal scores per coin per candle
CREATE TABLE IF NOT EXISTS signals (
    symbol              VARCHAR NOT NULL,
    timestamp           TIMESTAMP NOT NULL,
    -- Individual signal scores (0-100)
    trend_score         DOUBLE,
    rsi_score           DOUBLE,
    macd_score          DOUBLE,
    volume_score        DOUBLE,
    wyckoff_score       DOUBLE,
    onchain_score       DOUBLE,
    sentiment_score     DOUBLE,
    -- Weighted total
    total_score         DOUBLE,
    -- Meta
    regime              VARCHAR,
    fire                BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (symbol, timestamp)
);

-- Executed trades (paper + live)
CREATE TABLE IF NOT EXISTS trades (
    id              VARCHAR PRIMARY KEY,
    symbol          VARCHAR NOT NULL,
    side            VARCHAR NOT NULL,
    entry_price     DOUBLE,
    stop_price      DOUBLE,
    tp1_price       DOUBLE,
    tp2_price       DOUBLE,
    quantity        DOUBLE,
    risk_usd        DOUBLE,
    risk_pct        DOUBLE,
    score_at_entry  DOUBLE,
    regime          VARCHAR,
    engine          VARCHAR,
    status          VARCHAR DEFAULT 'open',
    -- Outcome
    exit_price      DOUBLE,
    pnl_usd         DOUBLE,
    pnl_pct         DOUBLE,
    r_multiple      DOUBLE,
    hold_hours      DOUBLE,
    -- Timestamps
    opened_at       TIMESTAMP DEFAULT now(),
    closed_at       TIMESTAMP,
    -- Paper vs live
    is_paper        BOOLEAN DEFAULT TRUE
);

-- On-chain metrics (BTC + ETH daily)
CREATE TABLE IF NOT EXISTS onchain (
    asset       VARCHAR NOT NULL,
    date        DATE NOT NULL,
    -- Exchange flows
    exch_inflow  DOUBLE,
    exch_outflow DOUBLE,
    exch_netflow DOUBLE,
    -- Holder metrics
    mvrv_ratio   DOUBLE,
    nupl         DOUBLE,
    -- Supply
    active_addr  DOUBLE,
    PRIMARY KEY (asset, date)
);

-- Macro data (weekly)
CREATE TABLE IF NOT EXISTS macro (
    date        DATE PRIMARY KEY,
    global_m2   DOUBLE,
    btc_dominance DOUBLE,
    fear_greed  INTEGER,
    dxy         DOUBLE
);

-- Portfolio snapshots (daily)
CREATE TABLE IF NOT EXISTS portfolio (
    date            DATE PRIMARY KEY,
    total_usd       DOUBLE,
    total_idr       DOUBLE,
    core_usd        DOUBLE,
    trading_usd     DOUBLE,
    cash_usd        DOUBLE,
    open_positions  INTEGER,
    daily_pnl_usd   DOUBLE,
    daily_pnl_pct   DOUBLE
);
"""

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


class Database:
    def __init__(self, path: str = DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = duckdb.connect(path)
        self._init_schema()
        logger.info(f"Database connected: {path}")

    def _init_schema(self):
        self.conn.execute(SCHEMA)
        self.conn.execute(SCHEMA_PHASE2)

    # ── Candles ──────────────────────────────────────────────

    def upsert_candles(self, symbol: str, timeframe: str, df: pd.DataFrame):
        """Insert or update OHLCV candles. df must have columns:
        timestamp, open, high, low, close, volume"""
        if df.empty:
            return
        df = df.copy()
        df["symbol"] = symbol
        df["timeframe"] = timeframe
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        self.conn.execute("""
            INSERT OR REPLACE INTO candles
            SELECT symbol, timeframe, timestamp, open, high, low, close, volume
            FROM df
        """)

    def get_candles(self, symbol: str, timeframe: str,
                    limit: int = 500) -> pd.DataFrame:
        return self.conn.execute("""
            SELECT timestamp, open, high, low, close, volume
            FROM candles
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, [symbol, timeframe, limit]).df().iloc[::-1].reset_index(drop=True)

    def get_latest_timestamp(self, symbol: str, timeframe: str):
        result = self.conn.execute("""
            SELECT MAX(timestamp) FROM candles
            WHERE symbol = ? AND timeframe = ?
        """, [symbol, timeframe]).fetchone()[0]
        return result

    # ── Indicators ───────────────────────────────────────────

    def upsert_indicators(self, symbol: str, timeframe: str, df: pd.DataFrame):
        if df.empty:
            return
        df = df.copy()
        df["symbol"] = symbol
        df["timeframe"] = timeframe
        self.conn.execute("""
            INSERT OR REPLACE INTO indicators
            SELECT symbol, timeframe, timestamp,
                   ema_20, ema_50, ema_200,
                   adx, adx_pos, adx_neg,
                   rsi_14, macd, macd_signal, macd_hist,
                   atr_14, bb_upper, bb_lower, bb_width,
                   vol_sma_20, vol_ratio
            FROM df
        """)

    def get_indicators(self, symbol: str, timeframe: str,
                       limit: int = 200) -> pd.DataFrame:
        return self.conn.execute("""
            SELECT * FROM indicators
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, [symbol, timeframe, limit]).df().iloc[::-1].reset_index(drop=True)

    # ── Signals ──────────────────────────────────────────────

    def upsert_signal(self, symbol: str, scores: dict, timestamp=None):
        if timestamp is None:
            timestamp = datetime.utcnow()
        self.conn.execute("""
            INSERT OR REPLACE INTO signals VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, [
            symbol, timestamp,
            scores.get("trend_score", 0),
            scores.get("rsi_score", 0),
            scores.get("macd_score", 0),
            scores.get("volume_score", 0),
            scores.get("wyckoff_score", 0),
            scores.get("onchain_score", 0),
            scores.get("sentiment_score", 0),
            scores.get("total_score", 0),
            scores.get("regime", "UNKNOWN"),
            scores.get("total_score", 0) >= 70
        ])

    def get_latest_signals(self) -> pd.DataFrame:
        return self.conn.execute("""
            SELECT s.*, c.close as current_price
            FROM signals s
            JOIN (
                SELECT symbol, close,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp DESC) as rn
                FROM candles WHERE timeframe = '4h'
            ) c ON s.symbol = c.symbol AND c.rn = 1
            WHERE s.timestamp = (
                SELECT MAX(timestamp) FROM signals s2
                WHERE s2.symbol = s.symbol
            )
            ORDER BY s.total_score DESC
        """).df()

    # ── Trades ───────────────────────────────────────────────

    def open_trade(self, trade: dict) -> str:
        import uuid
        trade_id = str(uuid.uuid4())[:8].upper()
        self.conn.execute("""
            INSERT INTO trades (id, symbol, side, entry_price, stop_price,
                               tp1_price, tp2_price, quantity, risk_usd,
                               risk_pct, score_at_entry, regime, engine,
                               is_paper)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            trade_id, trade["symbol"], trade["side"],
            trade["entry_price"], trade["stop_price"],
            trade["tp1_price"], trade["tp2_price"],
            trade["quantity"], trade["risk_usd"],
            trade["risk_pct"], trade["score"],
            trade["regime"], trade["engine"],
            trade.get("is_paper", True)
        ])
        return trade_id

    def close_trade(self, trade_id: str, exit_price: float, reason: str = ""):
        trade = self.conn.execute(
            "SELECT * FROM trades WHERE id = ?", [trade_id]
        ).df().iloc[0]

        entry = trade["entry_price"]
        qty   = trade["quantity"]
        pnl   = (exit_price - entry) * qty if trade["side"] == "buy" else (entry - exit_price) * qty
        pnl_pct = pnl / (entry * qty)
        r_mult  = pnl / trade["risk_usd"] if trade["risk_usd"] > 0 else 0

        self.conn.execute("""
            UPDATE trades SET
                status = 'closed', exit_price = ?, pnl_usd = ?,
                pnl_pct = ?, r_multiple = ?,
                hold_hours = DATEDIFF('hour', opened_at, now()),
                closed_at = now()
            WHERE id = ?
        """, [exit_price, pnl, pnl_pct, r_mult, trade_id])

        return {"pnl_usd": pnl, "pnl_pct": pnl_pct, "r_multiple": r_mult}

    def get_open_trades(self) -> pd.DataFrame:
        return self.conn.execute(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY opened_at"
        ).df()

    def get_trade_stats(self, days: int = 30) -> dict:
        result = self.conn.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                AVG(pnl_usd) as avg_pnl,
                AVG(r_multiple) as avg_r,
                SUM(pnl_usd) as total_pnl,
                MAX(pnl_usd) as best_trade,
                MIN(pnl_usd) as worst_trade
            FROM trades
            WHERE status = 'closed'
            AND opened_at >= now() - INTERVAL ? DAY
        """, [days]).df()
        row = result.iloc[0]
        win_rate = row["wins"] / row["total_trades"] if row["total_trades"] > 0 else 0
        return {
            "total":     int(row["total_trades"]),
            "wins":      int(row["wins"]),
            "win_rate":  round(win_rate * 100, 1),
            "avg_r":     round(row["avg_r"] or 0, 2),
            "total_pnl": round(row["total_pnl"] or 0, 2),
            "best":      round(row["best_trade"] or 0, 2),
            "worst":     round(row["worst_trade"] or 0, 2),
        }

    # ── Macro ─────────────────────────────────────────────────

    def upsert_macro(self, date, data: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO macro VALUES (?, ?, ?, ?, ?)
        """, [date, data.get("global_m2"), data.get("btc_dominance"),
              data.get("fear_greed"), data.get("dxy")])

    def get_latest_macro(self) -> dict:
        result = self.conn.execute("""
            SELECT * FROM macro ORDER BY date DESC LIMIT 1
        """).df()
        if result.empty:
            return {}
        return result.iloc[0].to_dict()

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

    def get_upcoming_unlocks(self, symbol: str, days: int = 30) -> list:
        result = self.conn.execute(f"""
            SELECT * FROM token_unlocks
            WHERE symbol = ?
              AND unlock_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL {int(days)} DAY
            ORDER BY unlock_date
        """, [symbol]).df()
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

    # ── Portfolio ─────────────────────────────────────────────

    def snapshot_portfolio(self, data: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO portfolio VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            datetime.utcnow().date(),
            data["total_usd"], data["total_idr"],
            data["core_usd"], data["trading_usd"],
            data["cash_usd"], data["open_positions"],
            data["daily_pnl_usd"], data["daily_pnl_pct"]
        ])

    def close(self):
        self.conn.close()


# Singleton
_db = None
def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db
