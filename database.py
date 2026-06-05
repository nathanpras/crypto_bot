# ============================================================
# database.py — DuckDB Storage Layer
# ============================================================

import duckdb
import pandas as pd
from datetime import datetime, timedelta
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

SCHEMA_PHASE3 = """
CREATE TABLE IF NOT EXISTS journal_trades (
    id                  VARCHAR PRIMARY KEY,
    symbol              VARCHAR NOT NULL,
    entry_price         DOUBLE NOT NULL,
    stop_price          DOUBLE NOT NULL,
    tp1_price           DOUBLE NOT NULL,
    tp2_price           DOUBLE NOT NULL,
    open_time           TIMESTAMP NOT NULL,
    close_time          TIMESTAMP,
    exit_price          DOUBLE,
    exit_reason         VARCHAR,
    pnl_usd             DOUBLE,
    pnl_idr             DOUBLE,
    r_multiple          DOUBLE,
    signal_score        DOUBLE,
    signal_id           VARCHAR,
    status              VARCHAR DEFAULT 'open',
    notes               VARCHAR,
    reminder_sent_at    TIMESTAMP
);
"""

SCHEMA_PHASE4 = """
CREATE TABLE IF NOT EXISTS coin_news (
    id              VARCHAR PRIMARY KEY,
    symbol          VARCHAR NOT NULL,
    published_at    TIMESTAMP NOT NULL,
    title           VARCHAR,
    sentiment       VARCHAR,
    vader_compound  DOUBLE,
    is_critical     BOOLEAN DEFAULT FALSE,
    votes_pos       INTEGER DEFAULT 0,
    votes_neg       INTEGER DEFAULT 0,
    source          VARCHAR
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

SCHEMA_PHASE8 = """
CREATE TABLE IF NOT EXISTS signal_registry (
    signal_name    VARCHAR PRIMARY KEY,
    category       VARCHAR,
    update_freq    VARCHAR,
    source         VARCHAR,
    enabled        BOOLEAN DEFAULT TRUE,
    last_updated   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS optimized_weights (
    regime         VARCHAR NOT NULL,
    signal_name    VARCHAR NOT NULL,
    weight         DOUBLE,
    fitness_score  DOUBLE,
    optimized_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (regime, signal_name, optimized_at)
);

CREATE TABLE IF NOT EXISTS liquidations (
    symbol         VARCHAR NOT NULL,
    timestamp      TIMESTAMP NOT NULL,
    liq_long_usd   DOUBLE,
    liq_short_usd  DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS lunarcrush_metrics (
    symbol         VARCHAR NOT NULL,
    timestamp      TIMESTAMP NOT NULL,
    galaxy_score   DOUBLE,
    alt_rank       INTEGER,
    social_volume  DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS google_trends (
    symbol         VARCHAR NOT NULL,
    date           DATE NOT NULL,
    interest       INTEGER,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS onchain_real (
    asset              VARCHAR NOT NULL,
    date               DATE NOT NULL,
    active_addr        BIGINT,
    tx_count           BIGINT,
    exchange_inflow    DOUBLE,
    exchange_outflow   DOUBLE,
    nvt_ratio          DOUBLE,
    PRIMARY KEY (asset, date)
);

CREATE TABLE IF NOT EXISTS reddit_sentiment (
    symbol         VARCHAR NOT NULL,
    date           DATE NOT NULL,
    post_count     INTEGER,
    avg_sentiment  DOUBLE,
    bullish_pct    DOUBLE,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS funding_history (
    symbol         VARCHAR NOT NULL,
    timestamp      TIMESTAMP NOT NULL,
    funding_rate   DOUBLE,
    PRIMARY KEY (symbol, timestamp)
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
        self.conn.execute(SCHEMA_PHASE3)
        self.conn.execute(SCHEMA_PHASE4)
        self.conn.execute(SCHEMA_PHASE5)
        self._migrate_phase7b()
        self.conn.execute(SCHEMA_PHASE8)

    def _migrate_phase7b(self):
        """Add vader_compound to coin_news if missing (Phase 7B migration)."""
        try:
            cols = [r[0] for r in self.conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'coin_news'"
            ).fetchall()]
            if "vader_compound" not in cols:
                self.conn.execute(
                    "ALTER TABLE coin_news ADD COLUMN vader_compound DOUBLE"
                )
                logger.info("Migrated coin_news: added vader_compound column")
        except Exception as e:
            logger.debug(f"Phase 7B migration skipped: {e}")

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
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
        days = max(0, int(days))
        result = self.conn.execute(f"""
            SELECT * FROM token_unlocks
            WHERE symbol = ?
              AND unlock_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL {days} DAY
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

    # ── Journal Trades ───────────────────────────────────────

    def open_journal_trade(self, symbol: str, entry_price: float,
                           stop_price: float, tp1_price: float,
                           tp2_price: float, signal_score, signal_id) -> str:
        import uuid
        trade_id  = str(uuid.uuid4())[:12]
        open_time = datetime.utcnow()
        self.conn.execute("""
            INSERT INTO journal_trades
                (id, symbol, entry_price, stop_price, tp1_price, tp2_price,
                 open_time, signal_score, signal_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """, [trade_id, symbol, entry_price, stop_price,
              tp1_price, tp2_price, open_time, signal_score, signal_id])
        return trade_id

    def close_journal_trade(self, trade_id: str, exit_price: float,
                            exit_reason: str, pnl_usd: float,
                            pnl_idr: float, r_multiple: float):
        self.conn.execute("""
            UPDATE journal_trades
            SET status='closed', close_time=?, exit_price=?,
                exit_reason=?, pnl_usd=?, pnl_idr=?, r_multiple=?
            WHERE id=?
        """, [datetime.utcnow(), exit_price, exit_reason,
              pnl_usd, pnl_idr, r_multiple, trade_id])

    def get_open_journal_trades(self) -> list:
        rows = self.conn.execute("""
            SELECT id, symbol, entry_price, stop_price, tp1_price, tp2_price,
                   open_time, signal_score, signal_id, reminder_sent_at, status
            FROM journal_trades WHERE status = 'open'
            ORDER BY open_time
        """).fetchall()
        cols = ["id", "symbol", "entry_price", "stop_price", "tp1_price",
                "tp2_price", "open_time", "signal_score", "signal_id",
                "reminder_sent_at", "status"]
        return [dict(zip(cols, row)) for row in rows]

    def get_open_journal_trades_by_symbol(self, symbol: str) -> list:
        rows = self.conn.execute("""
            SELECT id, symbol, entry_price, stop_price, tp1_price, tp2_price,
                   open_time, signal_score, signal_id, reminder_sent_at, status
            FROM journal_trades WHERE status = 'open' AND symbol = ?
            ORDER BY open_time
        """, [symbol]).fetchall()
        cols = ["id", "symbol", "entry_price", "stop_price", "tp1_price",
                "tp2_price", "open_time", "signal_score", "signal_id",
                "reminder_sent_at", "status"]
        return [dict(zip(cols, row)) for row in rows]

    def get_open_journal_trade_by_symbol(self, symbol: str):
        trades = self.get_open_journal_trades_by_symbol(symbol)
        return trades[-1] if trades else None

    def get_journal_trades_by_period(self, date_from: str, date_to: str):
        return self.conn.execute("""
            SELECT symbol, entry_price, exit_price, exit_reason,
                   pnl_usd, pnl_idr, r_multiple, signal_score,
                   open_time, close_time
            FROM journal_trades
            WHERE status = 'closed'
              AND close_time >= ? AND close_time <= ?
            ORDER BY close_time
        """, [date_from, date_to + " 23:59:59"]).df()

    def get_last_signal_for_symbol(self, symbol: str, within_hours: int = 48):
        cutoff = datetime.utcnow() - timedelta(hours=within_hours)
        result = self.conn.execute("""
            SELECT timestamp, total_score
            FROM signals
            WHERE symbol = ? AND timestamp >= ? AND fire = TRUE
            ORDER BY timestamp DESC LIMIT 1
        """, [symbol, cutoff]).fetchone()
        if result:
            ts = result[0]
            sid = f"{symbol[:3]}-{ts.strftime('%m%d')}" if hasattr(ts, 'strftime') else f"{symbol[:3]}-manual"
            return {"total_score": result[1], "signal_id": sid}
        return None

    def update_journal_reminder_sent(self, trade_id: str):
        self.conn.execute("""
            UPDATE journal_trades SET reminder_sent_at = ? WHERE id = ?
        """, [datetime.utcnow(), trade_id])

    def get_last_deployed_backtest(self):
        result = self.conn.execute("""
            SELECT val_win_rate, avg_r, val_sharpe
            FROM backtest_results
            WHERE deployed = TRUE
            ORDER BY run_date DESC LIMIT 1
        """).fetchone()
        if result:
            return {"val_win_rate": result[0], "avg_r": result[1], "val_sharpe": result[2]}
        return None

    def get_latest_price(self, symbol: str):
        result = self.conn.execute("""
            SELECT close FROM candles
            WHERE symbol = ? AND timeframe = '4h'
            ORDER BY timestamp DESC LIMIT 1
        """, [symbol]).fetchone()
        return result[0] if result else None

    # ── Phase 4: News & Options ───────────────────────────────────

    def upsert_coin_news(self, symbol: str, items: list):
        for item in items:
            self.conn.execute("""
                INSERT OR REPLACE INTO coin_news
                    (id, symbol, published_at, title, sentiment, vader_compound,
                     is_critical, votes_pos, votes_neg, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                item["id"], symbol,
                item.get("published_at", datetime.utcnow()),
                item.get("title", ""),
                item.get("sentiment", "neutral"),
                item.get("vader_compound"),   # None = not computed by VADER
                item.get("is_critical", False),
                item.get("votes_pos", 0),
                item.get("votes_neg", 0),
                item.get("source", ""),
            ])

    def get_recent_news(self, symbol: str, hours: int = 24) -> list:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        rows = self.conn.execute("""
            SELECT id, title, sentiment, vader_compound, is_critical, votes_pos, votes_neg
            FROM coin_news
            WHERE symbol = ? AND published_at >= ?
            ORDER BY published_at DESC
        """, [symbol, cutoff]).fetchall()
        cols = ["id", "title", "sentiment", "vader_compound",
                "is_critical", "votes_pos", "votes_neg"]
        return [dict(zip(cols, r)) for r in rows]

    def set_news_block(self, symbol: str, reason: str):
        from config import NEWS_BLOCK_HOURS
        now = datetime.utcnow()
        expires = now + timedelta(hours=NEWS_BLOCK_HOURS)
        self.conn.execute("""
            INSERT OR REPLACE INTO news_blocks
                (symbol, blocked_at, reason, expires_at, manual_unblock)
            VALUES (?, ?, ?, ?, FALSE)
        """, [symbol, now, reason, expires])

    def is_news_blocked(self, symbol: str):
        result = self.conn.execute("""
            SELECT reason, expires_at FROM news_blocks
            WHERE symbol = ? AND expires_at > ? AND manual_unblock = FALSE
        """, [symbol, datetime.utcnow()]).fetchone()
        if result:
            return {"reason": result[0], "expires_at": result[1]}
        return None

    def clear_news_block(self, symbol: str):
        self.conn.execute("""
            UPDATE news_blocks SET manual_unblock = TRUE WHERE symbol = ?
        """, [symbol])

    def upsert_options_metrics(self, symbol: str, metrics: dict):
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

    def get_latest_options(self, symbol: str):
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
        cutoff = datetime.utcnow() - timedelta(days=days)
        self.conn.execute(
            "DELETE FROM coin_news WHERE published_at < ?", [cutoff]
        )

    def upsert_social_metrics(self, symbol: str, metrics: dict):
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

    def get_latest_social(self, symbol: str):
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

    def get_whale_netflow_7d(self, symbol: str):
        asset = "BTC" if symbol == "BTCUSDT" else "ETH"
        cutoff = datetime.utcnow() - timedelta(days=7)
        result = self.conn.execute("""
            SELECT SUM(exch_netflow)
            FROM onchain
            WHERE asset = ? AND date >= ?
        """, [asset, cutoff.date()]).fetchone()
        return float(result[0]) if result and result[0] is not None else None

    def get_oi_change_7d(self, symbol: str):
        cutoff = datetime.utcnow() - timedelta(days=7)
        result = self.conn.execute("""
            SELECT
                FIRST(open_interest) AS oi_start,
                LAST(open_interest)  AS oi_end,
                AVG(funding_rate)    AS avg_funding
            FROM futures_metrics
            WHERE symbol = ? AND timestamp >= ?
        """, [symbol, cutoff]).fetchone()
        if result and result[0] and result[1] and result[0] > 0:
            oi_change_pct = (result[1] - result[0]) / result[0] * 100
            return {
                "oi_change_pct": round(oi_change_pct, 2),
                "avg_funding":   round(float(result[2] or 0), 5),
            }
        return None

    # ── Phase 8: Liquidations ────────────────────────────────────

    def upsert_liquidation(self, symbol: str, data: dict):
        ts = data.get("timestamp", datetime.utcnow())
        self.conn.execute("""
            INSERT OR REPLACE INTO liquidations
                (symbol, timestamp, liq_long_usd, liq_short_usd)
            VALUES (?, ?, ?, ?)
        """, [symbol, ts, data.get("liq_long_usd"), data.get("liq_short_usd")])

    def get_latest_liquidation(self, symbol: str, max_age_hours: int = 6):
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        result = self.conn.execute("""
            SELECT liq_long_usd, liq_short_usd, timestamp
            FROM liquidations
            WHERE symbol = ? AND timestamp >= ?
            ORDER BY timestamp DESC LIMIT 1
        """, [symbol, cutoff]).fetchone()
        if result:
            return {"liq_long_usd": result[0], "liq_short_usd": result[1], "timestamp": result[2]}
        return None

    # ── Phase 8: On-Chain Real ────────────────────────────────────

    def upsert_onchain_real(self, asset: str, data: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO onchain_real
                (asset, date, active_addr, tx_count, exchange_inflow, exchange_outflow, nvt_ratio)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            asset, data.get("date"),
            data.get("active_addr"), data.get("tx_count"),
            data.get("exchange_inflow"), data.get("exchange_outflow"),
            data.get("nvt_ratio"),
        ])

    def get_latest_onchain_real(self, asset: str, max_age_days: int = 2):
        cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).date()
        result = self.conn.execute("""
            SELECT active_addr, tx_count, exchange_inflow, exchange_outflow, nvt_ratio, date
            FROM onchain_real
            WHERE asset = ? AND date >= ?
            ORDER BY date DESC LIMIT 1
        """, [asset, cutoff]).fetchone()
        if result:
            return {
                "active_addr": result[0], "tx_count": result[1],
                "exchange_inflow": result[2], "exchange_outflow": result[3],
                "nvt_ratio": result[4], "date": result[5],
            }
        return None

    def get_onchain_real_history(self, asset: str, days: int = 30):
        result = self.conn.execute("""
            SELECT active_addr, tx_count, nvt_ratio, date
            FROM onchain_real
            WHERE asset = ?
            ORDER BY date DESC LIMIT ?
        """, [asset, days]).fetchall()
        return [{"active_addr": r[0], "tx_count": r[1], "nvt_ratio": r[2], "date": r[3]}
                for r in result]

    # ── Phase 8: LunarCrush ───────────────────────────────────────

    def upsert_lunarcrush(self, symbol: str, data: dict):
        ts = data.get("timestamp", datetime.utcnow())
        self.conn.execute("""
            INSERT OR REPLACE INTO lunarcrush_metrics
                (symbol, timestamp, galaxy_score, alt_rank, social_volume)
            VALUES (?, ?, ?, ?, ?)
        """, [symbol, ts, data.get("galaxy_score"), data.get("alt_rank"), data.get("social_volume")])

    def get_latest_lunarcrush(self, symbol: str, max_age_hours: int = 24):
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        result = self.conn.execute("""
            SELECT galaxy_score, alt_rank, social_volume, timestamp
            FROM lunarcrush_metrics
            WHERE symbol = ? AND timestamp >= ?
            ORDER BY timestamp DESC LIMIT 1
        """, [symbol, cutoff]).fetchone()
        if result:
            return {"galaxy_score": result[0], "alt_rank": result[1],
                    "social_volume": result[2], "timestamp": result[3]}
        return None

    # ── Phase 8: Google Trends ────────────────────────────────────

    def upsert_google_trends(self, symbol: str, data: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO google_trends (symbol, date, interest)
            VALUES (?, ?, ?)
        """, [symbol, data.get("date"), data.get("interest")])

    def get_latest_google_trends(self, symbol: str, max_age_days: int = 7):
        cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).date()
        result = self.conn.execute("""
            SELECT interest, date FROM google_trends
            WHERE symbol = ? AND date >= ?
            ORDER BY date DESC LIMIT 1
        """, [symbol, cutoff]).fetchone()
        if result:
            return {"interest": result[0], "date": result[1]}
        return None

    # ── Phase 8: Reddit Sentiment ─────────────────────────────────

    def upsert_reddit_sentiment(self, symbol: str, data: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO reddit_sentiment
                (symbol, date, post_count, avg_sentiment, bullish_pct)
            VALUES (?, ?, ?, ?, ?)
        """, [symbol, data.get("date"),
              data.get("post_count"), data.get("avg_sentiment"), data.get("bullish_pct")])

    def get_latest_reddit_sentiment(self, symbol: str, max_age_days: int = 2):
        cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).date()
        result = self.conn.execute("""
            SELECT post_count, avg_sentiment, bullish_pct, date
            FROM reddit_sentiment
            WHERE symbol = ? AND date >= ?
            ORDER BY date DESC LIMIT 1
        """, [symbol, cutoff]).fetchone()
        if result:
            return {"post_count": result[0], "avg_sentiment": result[1],
                    "bullish_pct": result[2], "date": result[3]}
        return None

    # ── Phase 8: Funding History ──────────────────────────────────

    def upsert_funding_history(self, symbol: str, data: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO funding_history (symbol, timestamp, funding_rate)
            VALUES (?, ?, ?)
        """, [symbol, data.get("timestamp", datetime.utcnow()), data.get("funding_rate")])

    def get_funding_history(self, symbol: str, limit: int = 720):
        result = self.conn.execute("""
            SELECT funding_rate, timestamp FROM funding_history
            WHERE symbol = ?
            ORDER BY timestamp DESC LIMIT ?
        """, [symbol, limit]).fetchall()
        return [{"funding_rate": r[0], "timestamp": r[1]} for r in result]

    def get_funding_30d_ma(self, symbol: str) -> float:
        result = self.conn.execute("""
            SELECT AVG(funding_rate) FROM funding_history
            WHERE symbol = ?
              AND timestamp >= now() - INTERVAL 30 DAY
        """, [symbol]).fetchone()
        return float(result[0]) if result and result[0] is not None else 0.0

    # ── Phase 8: Optimized Weights ────────────────────────────────

    def save_optimized_weights(self, regime: str, weights: dict, fitness_score: float = 0.0):
        ts = datetime.utcnow()
        for signal_name, weight in weights.items():
            self.conn.execute("""
                INSERT OR REPLACE INTO optimized_weights
                    (regime, signal_name, weight, fitness_score, optimized_at)
                VALUES (?, ?, ?, ?, ?)
            """, [regime, signal_name, weight, fitness_score, ts])

    def get_optimized_weights(self, regime: str) -> dict:
        latest = self.conn.execute("""
            SELECT MAX(optimized_at) FROM optimized_weights WHERE regime = ?
        """, [regime]).fetchone()[0]
        if latest is None:
            return {}
        rows = self.conn.execute("""
            SELECT signal_name, weight FROM optimized_weights
            WHERE regime = ? AND optimized_at = ?
        """, [regime, latest]).fetchall()
        return {r[0]: r[1] for r in rows}


# Singleton
_db = None
def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db
