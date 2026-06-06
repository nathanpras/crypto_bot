# collector/onchain_enhanced.py
"""
Enhanced on-chain data collection for all 19 coins.
BTC/ETH: CoinMetrics community API (MVRV, exchange netflow)
All coins: Bybit Futures API (OI, funding rate, long/short ratio, liquidations)
"""
import requests
import time
from datetime import date
from loguru import logger

from config import COINS, FUTURES_SCORING
from database import get_db


BYBIT_FUTURES_BASE = "https://api.bytick.com/v5/market"
COINMETRICS_BASE = "https://community-api.coinmetrics.io/v4"


# ── Bybit Futures Data ───────────────────────────────────────

def fetch_open_interest(symbol: str) -> dict:
    """Fetch current OI + 24h change dari Bybit Futures."""
    try:
        r = requests.get(
            f"{BYBIT_FUTURES_BASE}/open-interest",
            params={"category": "linear", "symbol": symbol,
                    "intervalTime": "1h", "limit": 25},
            timeout=10
        )
        items = r.json().get("result", {}).get("list", [])
        if not items:
            return {"open_interest": None, "oi_change_24h_pct": 0.0}

        current_oi = float(items[0]["openInterest"])
        if len(items) >= 24:
            oi_24h_ago = float(items[23]["openInterest"])
            oi_change_pct = (
                (current_oi - oi_24h_ago) / oi_24h_ago * 100
                if oi_24h_ago > 0 else 0.0
            )
        else:
            oi_change_pct = 0.0

        return {
            "open_interest":      current_oi,
            "oi_change_24h_pct":  round(oi_change_pct, 2),
        }
    except Exception as e:
        logger.debug(f"Bybit OI fetch failed for {symbol}: {e}")
        return {"open_interest": None, "oi_change_24h_pct": 0.0}


def fetch_funding_rate(symbol: str) -> float:
    """Fetch current funding rate dari Bybit Futures tickers."""
    try:
        r = requests.get(
            f"{BYBIT_FUTURES_BASE}/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=10
        )
        items = r.json().get("result", {}).get("list", [])
        if items:
            return float(items[0].get("fundingRate", 0) or 0)
        return 0.0
    except Exception as e:
        logger.debug(f"Bybit funding rate fetch failed for {symbol}: {e}")
        return 0.0


def fetch_long_short_ratio(symbol: str) -> float:
    """Fetch long/short account ratio dari Bybit Futures."""
    try:
        r = requests.get(
            f"{BYBIT_FUTURES_BASE}/account-ratio",
            params={"category": "linear", "symbol": symbol,
                    "period": "1h", "limit": 1},
            timeout=10
        )
        items = r.json().get("result", {}).get("list", [])
        if items:
            buy  = float(items[0].get("buyRatio",  0.5) or 0.5)
            sell = float(items[0].get("sellRatio", 0.5) or 0.5)
            return round(buy / sell, 3) if sell > 0 else 1.0
        return 1.0
    except Exception as e:
        logger.debug(f"Bybit L/S ratio fetch failed for {symbol}: {e}")
        return 1.0


def fetch_liquidations(symbol: str) -> dict:
    """
    Estimate liquidation pressure from OI delta + price movement.
    Bybit has no public liquidation endpoint, so we use a proxy:
    - OI drops sharply + price drops = long liquidations
    - OI drops sharply + price rises = short liquidations
    Uses last 2 OI data points and latest ticker price.
    """
    try:
        # Get last 2 hours of OI to measure delta
        r_oi = requests.get(
            f"{BYBIT_FUTURES_BASE}/open-interest",
            params={"category": "linear", "symbol": symbol,
                    "intervalTime": "1h", "limit": 2},
            timeout=10
        )
        oi_items = r_oi.json().get("result", {}).get("list", [])

        # Get last price from ticker
        r_tick = requests.get(
            f"{BYBIT_FUTURES_BASE}/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=10
        )
        tick_items = r_tick.json().get("result", {}).get("list", [])

        if len(oi_items) < 2 or not tick_items:
            return {"liq_long_24h": 0.0, "liq_short_24h": 0.0}

        oi_now  = float(oi_items[0]["openInterest"])
        oi_prev = float(oi_items[1]["openInterest"])
        price_chg = float(tick_items[0].get("price24hPcnt", 0) or 0)

        oi_delta_pct = (oi_now - oi_prev) / oi_prev * 100 if oi_prev > 0 else 0.0
        oi_usd       = oi_now * float(tick_items[0].get("lastPrice", 0) or 0)

        # Large OI drop (>2%) = forced liquidations happening
        liq_long, liq_short = 0.0, 0.0
        if oi_delta_pct < -2.0:
            if price_chg < 0:
                liq_long  = abs(oi_delta_pct) / 100 * oi_usd  # price fell + OI down = longs liquidated
            else:
                liq_short = abs(oi_delta_pct) / 100 * oi_usd  # price rose + OI down = shorts liquidated

        return {"liq_long_24h": liq_long, "liq_short_24h": liq_short}

    except Exception as e:
        logger.debug(f"Liquidation estimate failed for {symbol}: {e}")
        return {"liq_long_24h": 0.0, "liq_short_24h": 0.0}


def fetch_all_futures_data(symbol: str) -> dict:
    """Fetch semua Bybit Futures data untuk satu coin."""
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
        from datetime import datetime, timedelta
        start = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
        r = requests.get(
            f"{COINMETRICS_BASE}/timeseries/asset-metrics",
            params={
                "assets":     asset,
                "metrics":    "CapMVRVCur,FlowNetInvNtv",
                "frequency":  "1d",
                "start_time": start,
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
    Hitung on-chain score 0-100 dari Bybit Futures data.
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
    # liq_long and liq_short are mutually exclusive (set by price direction in fetch_liquidations)
    if liq_l > 5_000_000:
        score = max(score, 75)    # Heavy long liquidation = capitulation bottom signal
    elif liq_s > 5_000_000:
        score = min(score + 5, 90)  # Short squeeze = additional bullish momentum

    return float(max(0.0, min(100.0, score)))


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
    full=False: hanya Bybit Futures (cepat, ~30 detik)
    full=True:  Futures + CoinMetrics (semua, ~2 menit)
    """
    db = get_db()

    logger.info(f"Collecting on-chain data (full={full})...")

    for symbol in COINS:
        try:
            data = fetch_all_futures_data(symbol)
            db.upsert_futures_metrics(symbol, data)
            logger.debug(f"  {symbol}: funding={data.get('funding_rate', 0):.4f}, "
                         f"OI_chg={data.get('oi_change_24h_pct', 0):.1f}%")
            time.sleep(0.2)
        except Exception as e:
            logger.warning(f"  Failed to collect futures for {symbol}: {e}")

    if full:
        for asset in ["btc", "eth"]:
            try:
                data = fetch_coinmetrics(asset)
                if data:
                    db.conn.execute("""
                        INSERT OR REPLACE INTO onchain
                        (asset, date, exch_netflow, mvrv_ratio)
                        VALUES (?, CURRENT_DATE, ?, ?)
                    """, [asset, data.get("exch_netflow"), data.get("mvrv_ratio")])
                    logger.info(f"  CoinMetrics {asset}: MVRV={data.get('mvrv_ratio', '?'):.2f}")
                time.sleep(7)
            except Exception as e:
                logger.warning(f"  CoinMetrics failed for {asset}: {e}")

    logger.info("On-chain collection complete.")


def get_mvrv_score(symbol: str, db) -> float:
    """O1: MVRV score. Uses onchain table if available, else 365-day price MA proxy."""
    asset_map = {"BTCUSDT": "btc", "ETHUSDT": "eth"}
    asset = asset_map.get(symbol)
    if not asset:
        return 50.0

    # Try CoinMetrics data first
    try:
        result = db.conn.execute("""
            SELECT mvrv_ratio FROM onchain WHERE asset = ?
            ORDER BY date DESC LIMIT 1
        """, [asset]).fetchone()
        if result and result[0] is not None:
            mvrv = float(result[0])
            if mvrv < 0.8:    return 88.0
            elif mvrv < 1.0:  return 78.0
            elif mvrv < 1.5:  return 62.0
            elif mvrv < 2.0:  return 50.0
            elif mvrv < 2.5:  return 38.0
            elif mvrv < 3.0:  return 25.0
            else:              return 12.0
    except Exception:
        pass

    # Fallback: price / 365-day avg as MVRV proxy
    try:
        rows = db.conn.execute("""
            SELECT close FROM candles
            WHERE symbol = ? AND timeframe = '1d'
            ORDER BY timestamp DESC LIMIT 365
        """, [symbol]).fetchall()
        if len(rows) < 30:
            return 50.0
        prices = [float(r[0]) for r in rows]
        current = prices[0]
        avg = sum(prices) / len(prices)
        mvrv_proxy = current / avg
        if mvrv_proxy < 0.7:    return 88.0
        elif mvrv_proxy < 0.85: return 75.0
        elif mvrv_proxy < 1.0:  return 62.0
        elif mvrv_proxy < 1.3:  return 50.0
        elif mvrv_proxy < 1.6:  return 38.0
        elif mvrv_proxy < 2.0:  return 25.0
        else:                    return 12.0
    except Exception:
        return 50.0


def get_netflow_score(symbol: str, db) -> float:
    """O2: Netflow score. Uses onchain table if available, else 30-day price trend proxy."""
    asset_map = {"BTCUSDT": "btc", "ETHUSDT": "eth"}
    asset = asset_map.get(symbol)
    if not asset:
        return 50.0

    # Try CoinMetrics data first
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(days=7)).date()
        result = db.conn.execute("""
            SELECT AVG(exch_netflow) FROM onchain
            WHERE asset = ? AND date >= ?
        """, [asset, cutoff]).fetchone()
        if result and result[0] is not None:
            netflow = float(result[0])
            if netflow < -5000:   return 85.0
            elif netflow < -1000: return 72.0
            elif netflow < 0:     return 60.0
            elif netflow < 1000:  return 45.0
            elif netflow < 5000:  return 30.0
            else:                  return 15.0
    except Exception:
        pass

    # Fallback: 30-day price trend as netflow proxy
    # Rising price trend = likely accumulation (outflow) = bullish
    try:
        rows = db.conn.execute("""
            SELECT close FROM candles
            WHERE symbol = ? AND timeframe = '1d'
            ORDER BY timestamp DESC LIMIT 30
        """, [symbol]).fetchall()
        if len(rows) < 14:
            return 50.0
        prices = [float(r[0]) for r in rows]
        chg_30d = (prices[0] - prices[-1]) / prices[-1] * 100
        if chg_30d > 20:    return 72.0
        elif chg_30d > 8:   return 62.0
        elif chg_30d > 2:   return 55.0
        elif chg_30d > -2:  return 48.0
        elif chg_30d > -8:  return 38.0
        elif chg_30d > -20: return 28.0
        else:                return 18.0
    except Exception:
        return 50.0
