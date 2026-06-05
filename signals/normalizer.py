# signals/normalizer.py
"""
Universal signal normalizer — computes all 32 registered signals (0-100).
Returns dict {signal_id: float} for use in weighted sum engine.
"""

import pandas as pd
from loguru import logger

from signals.technical import (
    calc_trend_score, calc_rsi_score, calc_macd_score,
    calc_volume_score, calc_wyckoff_score,
    calc_vwap_normalized, calc_volume_delta_normalized, calc_bb_squeeze_normalized,
)
from collector.orderbook import fetch_orderbook_imbalance, get_orderbook_score
from collector.funding_history import get_funding_oscillator_score
from collector.onchain_enhanced import get_mvrv_score, get_netflow_score
from collector.onchain_real import get_real_onchain_score, compute_nvt_score
from collector.liquidations import get_liquidation_cascade_score
from collector.social_lunar import get_lunarcrush_score, get_google_trends_score, get_reddit_sentiment_score
from collector.macro_extended import get_altseason_index, get_dex_cex_ratio_score


def _fear_greed_score(fear_greed: int) -> float:
    """S1: Contrarian F&G — extreme fear = bullish, extreme greed = bearish."""
    if fear_greed < 20:    return 85.0
    elif fear_greed < 35:  return 70.0
    elif fear_greed < 50:  return 55.0
    elif fear_greed < 65:  return 45.0
    elif fear_greed < 80:  return 30.0
    else:                   return 15.0


def _news_sentiment_score(symbol: str, db) -> float:
    """S2: VADER news sentiment rolling 24h from coin_news table."""
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(hours=24)
    try:
        rows = db.conn.execute("""
            SELECT vader_compound FROM coin_news
            WHERE symbol = ? AND published_at >= ? AND vader_compound IS NOT NULL
        """, [symbol, cutoff]).fetchall()
        if not rows:
            return 50.0
        avg = sum(r[0] for r in rows) / len(rows)
        return float(max(0.0, min(100.0, (avg + 1) / 2 * 100)))
    except Exception:
        return 50.0


def _social_coingecko_score(symbol: str, db) -> float:
    """S3: Social score from CoinGecko (social_metrics table)."""
    try:
        result = db.conn.execute("""
            SELECT social_score FROM social_metrics
            WHERE symbol = ? ORDER BY date DESC LIMIT 1
        """, [symbol]).fetchone()
        if not result or result[0] is None:
            return 50.0
        return float(max(0.0, min(100.0, float(result[0]))))
    except Exception:
        return 50.0


def _oi_funding_score(symbol: str, db) -> float:
    """D1: OI change 24h + funding rate from futures_metrics table."""
    try:
        result = db.conn.execute("""
            SELECT funding_rate, oi_change_24h_pct FROM futures_metrics
            WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1
        """, [symbol]).fetchone()
        if not result:
            return 50.0
        funding = float(result[0] or 0)
        oi_change = float(result[1] or 0)

        if funding < -0.05:   f_score = 80
        elif funding < -0.01: f_score = 68
        elif funding < 0.01:  f_score = 52
        elif funding < 0.05:  f_score = 35
        else:                  f_score = 18

        if oi_change > 15:    oi_score = 70
        elif oi_change > 5:   oi_score = 58
        elif oi_change > -5:  oi_score = 50
        elif oi_change > -15: oi_score = 40
        else:                  oi_score = 30

        return float(max(0.0, min(100.0, f_score * 0.6 + oi_score * 0.4)))
    except Exception:
        return 50.0


def _long_short_ratio_score(symbol: str, db) -> float:
    """D2: Long/Short ratio — contrarian. Extreme longs → bearish."""
    try:
        result = db.conn.execute("""
            SELECT long_short_ratio FROM futures_metrics
            WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1
        """, [symbol]).fetchone()
        if not result or result[0] is None:
            return 50.0
        ls = float(result[0])
        if ls < 0.7:   return 82.0
        elif ls < 0.9: return 68.0
        elif ls < 1.2: return 52.0
        elif ls < 1.8: return 38.0
        else:           return 20.0
    except Exception:
        return 50.0


def _options_score(symbol: str, db) -> float:
    """D3: Options put/call ratio — from options_metrics table."""
    try:
        result = db.conn.execute("""
            SELECT put_call_ratio, skew_25d FROM options_metrics
            WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1
        """, [symbol]).fetchone()
        if not result:
            return 50.0
        pc = float(result[0] or 1.0)
        skew = float(result[1] or 0)

        if pc < 0.7 and skew < -3:   return 85.0
        elif pc < 1.0:                return 65.0
        elif pc < 1.3:                return 50.0
        elif pc < 1.5:                return 35.0
        else:                          return 18.0
    except Exception:
        return 50.0


def _futures_basis_score(symbol: str, db) -> float:
    """D4: Futures basis stub — returns 50 until basis data is populated."""
    return 50.0


def _stablecoin_score(db) -> float:
    """M1: Stablecoin supply change — from macro table if available.
    NOTE: macro table does not currently have stablecoin_change_7d,
    so this falls back to 50.0 until the column is added."""
    try:
        result = db.conn.execute("""
            SELECT stablecoin_change_7d FROM macro
            ORDER BY date DESC LIMIT 1
        """).fetchone()
        if not result or result[0] is None:
            return 50.0
        chg = float(result[0])
        if chg > 3:    return 75.0
        elif chg > 1:  return 62.0
        elif chg > -1: return 50.0
        elif chg > -3: return 38.0
        else:           return 25.0
    except Exception:
        return 50.0


def _tvl_narrative_score(symbol: str, db) -> float:
    """M2: TVL narrative stub — returns 50 until sector TVL data is available."""
    return 50.0


def _global_macro_score(db) -> float:
    """M5: Global macro — from macro table if available, else 50."""
    try:
        result = db.conn.execute("""
            SELECT global_m2 FROM macro ORDER BY date DESC LIMIT 1
        """).fetchone()
        if not result or result[0] is None:
            return 50.0
        return 50.0  # Will be enhanced with FRED data when available
    except Exception:
        return 50.0


def _perp_spot_ratio_score(symbol: str, db) -> float:
    """O7: Perp volume / spot proxy using OI change."""
    try:
        result = db.conn.execute("""
            SELECT oi_change_24h_pct FROM futures_metrics
            WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1
        """, [symbol]).fetchone()
        if not result or result[0] is None:
            return 50.0
        oi_chg = float(result[0])
        if oi_chg > 20:    return 72.0
        elif oi_chg > 10:  return 62.0
        elif oi_chg > -10: return 50.0
        elif oi_chg > -20: return 38.0
        else:               return 28.0
    except Exception:
        return 50.0


def get_all_signals(symbol: str, db, fear_greed: int = 50,
                    funding_rate: float = 0.0) -> dict:
    """
    Compute all 32 registered signals for one coin.
    Returns {signal_id: float 0-100}. Returns 50.0 for any unavailable signal.
    Never raises — all exceptions caught and return neutral.
    """
    df_4h = db.get_candles(symbol, "4h", limit=220)
    df_1d = db.get_candles(symbol, "1d", limit=60)

    def safe(fn, *args, default=50.0):
        try:
            v = fn(*args)
            return float(max(0.0, min(100.0, v)))
        except Exception as e:
            logger.debug(f"Signal calc error in {fn.__name__}: {e}")
            return default

    scores = {}

    # ── Technical (T1-T10) ──────────────────────────────────────
    scores["T1"]  = safe(calc_trend_score, df_4h, df_1d)
    scores["T2"]  = safe(calc_rsi_score, df_4h)
    scores["T3"]  = safe(calc_macd_score, df_4h)
    scores["T4"]  = safe(calc_volume_score, df_4h)
    scores["T5"]  = safe(calc_wyckoff_score, df_4h)
    scores["T6"]  = safe(calc_vwap_normalized, df_4h)
    scores["T7"]  = safe(calc_volume_delta_normalized, df_4h)
    scores["T8"]  = safe(calc_bb_squeeze_normalized, df_4h)

    try:
        ratio = fetch_orderbook_imbalance(symbol)
        scores["T9"] = get_orderbook_score(symbol, ratio)
    except Exception:
        scores["T9"] = 50.0

    scores["T10"] = safe(get_funding_oscillator_score, symbol, db)

    # ── On-Chain (O1-O7) ───────────────────────────────────────
    scores["O1"] = safe(get_mvrv_score, symbol, db)
    scores["O2"] = safe(get_netflow_score, symbol, db)
    scores["O3"] = safe(get_real_onchain_score, symbol, db) if symbol == "BTCUSDT" else 50.0
    scores["O4"] = safe(get_real_onchain_score, symbol, db) if symbol == "ETHUSDT" else 50.0
    scores["O5"] = safe(get_liquidation_cascade_score, symbol, db)
    if symbol == "BTCUSDT":
        scores["O6"] = safe(compute_nvt_score, "BTC", db)
    elif symbol == "ETHUSDT":
        scores["O6"] = safe(compute_nvt_score, "ETH", db)
    else:
        scores["O6"] = 50.0
    scores["O7"] = safe(_perp_spot_ratio_score, symbol, db)

    # ── Sentiment (S1-S6) ──────────────────────────────────────
    scores["S1"] = _fear_greed_score(fear_greed)
    scores["S2"] = safe(_news_sentiment_score, symbol, db)
    scores["S3"] = safe(_social_coingecko_score, symbol, db)
    scores["S4"] = safe(get_lunarcrush_score, symbol, db)
    scores["S5"] = safe(get_google_trends_score, symbol, db)
    scores["S6"] = safe(get_reddit_sentiment_score, symbol, db)

    # ── Derivatives (D1-D4) ────────────────────────────────────
    scores["D1"] = safe(_oi_funding_score, symbol, db)
    scores["D2"] = safe(_long_short_ratio_score, symbol, db)
    scores["D3"] = safe(_options_score, symbol, db)
    scores["D4"] = safe(_futures_basis_score, symbol, db)

    # ── Macro (M1-M5) ──────────────────────────────────────────
    scores["M1"] = safe(_stablecoin_score, db)
    scores["M2"] = safe(_tvl_narrative_score, symbol, db)

    try:
        from config import COINS
        current_prices = {}
        for sym in list(COINS.keys())[:10]:
            try:
                result = db.conn.execute("""
                    SELECT close FROM candles
                    WHERE symbol = ? AND timeframe = '4h'
                    ORDER BY timestamp DESC LIMIT 1
                """, [sym]).fetchone()
                if result:
                    current_prices[sym] = float(result[0])
            except Exception:
                pass
        scores["M3"] = get_altseason_index(current_prices) if len(current_prices) >= 3 else 50.0
    except Exception:
        scores["M3"] = 50.0

    scores["M4"] = safe(get_dex_cex_ratio_score)
    scores["M5"] = safe(_global_macro_score, db)

    return scores
