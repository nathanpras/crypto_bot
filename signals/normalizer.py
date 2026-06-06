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
from collector.funding_history import get_funding_oscillator_score, get_funding_oscillator_score_bybit
from collector.onchain_enhanced import get_mvrv_score, get_netflow_score
from collector.onchain_real import get_real_onchain_score, compute_nvt_score
from collector.liquidations import get_liquidation_cascade_score, get_liquidation_score_bybit
from collector.social_lunar import get_lunarcrush_score, get_google_trends_score, get_reddit_sentiment_score, get_social_score_coingecko, get_stocktwits_sentiment_score
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
    """S2: News headline sentiment from CoinDesk+CoinTelegraph RSS using VADER."""
    import requests
    import xml.etree.ElementTree as ET

    keyword_map = {
        "BTCUSDT": ["bitcoin", "btc"],
        "ETHUSDT": ["ethereum", "eth"],
        "SOLUSDT": ["solana", "sol"],
        "XRPUSDT": ["xrp", "ripple"],
        "BNBUSDT": ["bnb", "binance"],
        "ADAUSDT": ["cardano", "ada"],
        "AVAXUSDT": ["avalanche", "avax"],
        "LINKUSDT": ["chainlink", "link"],
        "DOTUSDT": ["polkadot", "dot"],
        "ARBUSDT": ["arbitrum", "arb"],
        "OPUSDT": ["optimism"],
        "NEARUSDT": ["near"],
        "INJUSDT": ["injective", "inj"],
        "SUIUSDT": ["sui"],
        "APTUSDT": ["aptos", "apt"],
    }
    keywords = keyword_map.get(symbol, [symbol.replace("USDT", "").lower()])

    RSS_FEEDS = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ]

    headlines = []
    for feed_url in RSS_FEEDS:
        try:
            r = requests.get(feed_url, timeout=10, headers={"User-Agent": "APEX/1.0"})
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item"):
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    headlines.append(title_el.text.lower())
        except Exception:
            continue

    if not headlines:
        return 50.0

    # Filter to coin-specific headlines; fall back to all if too few
    relevant = [h for h in headlines if any(kw in h for kw in keywords)]
    sample = relevant if len(relevant) >= 3 else headlines[:30]

    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        analyzer = SentimentIntensityAnalyzer()
        scores_list = [analyzer.polarity_scores(h)["compound"] for h in sample]
        avg = sum(scores_list) / len(scores_list)
        score = (avg + 1) / 2 * 100  # map [-1,1] → [0,100]
        return float(max(10.0, min(90.0, score)))
    except Exception:
        return 50.0


def _social_coingecko_score(symbol: str, db) -> float:
    """S3: Social relevance from CoinGecko market cap rank + 7d momentum."""
    import requests
    from collector.social_lunar import COINGECKO_ID_MAP
    coin_id = COINGECKO_ID_MAP.get(symbol)
    if not coin_id:
        return 50.0
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            params={"localization": "false", "tickers": "false",
                    "market_data": "true", "community_data": "false",
                    "developer_data": "false"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        mkt = data.get("market_data", {})
        rank = int(data.get("market_cap_rank") or 50)
        chg_7d = float(mkt.get("price_change_percentage_7d") or 0)

        # Higher rank (lower number) = more social attention
        if rank <= 5:      rank_score = 70
        elif rank <= 20:   rank_score = 62
        elif rank <= 50:   rank_score = 55
        elif rank <= 100:  rank_score = 48
        else:              rank_score = 42

        # 7d momentum as trend confirmation
        if chg_7d > 15:    trend = 80
        elif chg_7d > 7:   trend = 68
        elif chg_7d > 2:   trend = 58
        elif chg_7d > -2:  trend = 50
        elif chg_7d > -7:  trend = 38
        elif chg_7d > -15: trend = 28
        else:              trend = 18

        return float(max(0.0, min(100.0, rank_score * 0.4 + trend * 0.6)))
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
    """D3: Options put/call OI ratio from Deribit. High puts = fear = contrarian bullish."""
    import requests

    # Only supported for BTC and ETH (Deribit BTC/ETH options)
    currency_map = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}
    currency = currency_map.get(symbol)
    if not currency:
        return 50.0

    try:
        r = requests.get(
            "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
            params={"currency": currency, "kind": "option"},
            timeout=12,
        )
        r.raise_for_status()
        results = r.json().get("result", [])
        if not results:
            return 50.0

        put_oi = 0.0
        call_oi = 0.0
        for item in results:
            name = item.get("instrument_name", "")
            oi = float(item.get("open_interest", 0) or 0)
            if name.endswith("-P"):
                put_oi += oi
            elif name.endswith("-C"):
                call_oi += oi

        if call_oi <= 0:
            return 50.0

        pc_ratio = put_oi / call_oi

        # Contrarian: high put/call = fear = bullish signal
        if pc_ratio > 1.5:    return 82.0   # Extreme fear / hedging = capitulation bottom
        elif pc_ratio > 1.2:  return 68.0
        elif pc_ratio > 0.9:  return 55.0   # Slightly more puts = mild fear
        elif pc_ratio > 0.7:  return 45.0
        elif pc_ratio > 0.5:  return 30.0   # Heavy call buying = complacency
        else:                  return 15.0   # Extreme call buying = top signal
    except Exception:
        return 50.0


def _futures_basis_score(symbol: str, db) -> float:
    """D4: Futures basis (contango/backwardation). Positive basis = bearish, negative = bullish."""
    import requests
    try:
        # Get perp mark price
        r_perp = requests.get(
            "https://api.bytick.com/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=8,
        )
        perp_items = r_perp.json().get("result", {}).get("list", [])
        if not perp_items:
            return 50.0
        mark_price = float(perp_items[0].get("markPrice") or perp_items[0].get("lastPrice", 0))

        # Get spot last price
        spot_symbol = symbol.replace("USDT", "") + "USDT"  # Same for most, but spot category
        r_spot = requests.get(
            "https://api.bytick.com/v5/market/tickers",
            params={"category": "spot", "symbol": spot_symbol},
            timeout=8,
        )
        spot_items = r_spot.json().get("result", {}).get("list", [])
        if not spot_items:
            return 50.0
        spot_price = float(spot_items[0].get("lastPrice", 0))

        if spot_price <= 0 or mark_price <= 0:
            return 50.0

        basis_pct = (mark_price - spot_price) / spot_price * 100

        # Score: negative basis (backwardation) = bullish → high score
        # Strong contango (>0.5%) → bearish → low score
        if basis_pct < -0.3:    return 85.0   # Strong backwardation = bullish
        elif basis_pct < -0.1:  return 68.0
        elif basis_pct < 0.1:   return 52.0   # Near-parity = neutral
        elif basis_pct < 0.3:   return 38.0
        elif basis_pct < 0.5:   return 28.0
        else:                    return 15.0   # High contango = bearish
    except Exception:
        return 50.0


def _stablecoin_score(db) -> float:
    """M1: Stablecoin supply 7d change — rising supply = more capital entering crypto = bullish."""
    import requests
    try:
        r = requests.get("https://stablecoins.llama.fi/stablecoins", timeout=10)
        r.raise_for_status()
        coins = r.json().get("peggedAssets", [])
        if not coins:
            return 50.0

        total_now = 0.0
        total_7d_ago = 0.0
        for coin in coins:
            circ = coin.get("circulating", {})
            circ_prev = coin.get("circulatingPrevWeek", {})
            # Values are dicts like {"peggedUSD": 1234567}
            now_usd = float(circ.get("peggedUSD", 0) or 0)
            prev_usd = float(circ_prev.get("peggedUSD", 0) or 0)
            total_now += now_usd
            total_7d_ago += prev_usd

        if total_7d_ago <= 0:
            return 50.0

        chg_pct = (total_now - total_7d_ago) / total_7d_ago * 100

        # Rising stablecoin supply = capital entering crypto = bullish
        if chg_pct > 3:    return 78.0
        elif chg_pct > 1:  return 65.0
        elif chg_pct > 0:  return 55.0
        elif chg_pct > -1: return 45.0
        elif chg_pct > -3: return 32.0
        else:               return 20.0
    except Exception:
        return 50.0


def _tvl_narrative_score(symbol: str, db) -> float:
    """M2: Global DeFi TVL 7d change — rising TVL = bullish narrative."""
    import requests
    try:
        r = requests.get("https://api.llama.fi/v2/historicalChainTvl", timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data or len(data) < 8:
            return 50.0

        # Most recent day vs 7 days ago
        latest = float(data[-1].get("tvl", 0))
        week_ago = float(data[-8].get("tvl", latest))

        if week_ago <= 0:
            return 50.0

        chg_pct = (latest - week_ago) / week_ago * 100

        if chg_pct > 8:    return 80.0
        elif chg_pct > 4:  return 68.0
        elif chg_pct > 1:  return 58.0
        elif chg_pct > -1: return 50.0
        elif chg_pct > -4: return 38.0
        elif chg_pct > -8: return 28.0
        else:               return 15.0
    except Exception:
        return 50.0


def _global_macro_score(db) -> float:
    """M5: Global crypto macro — BTC dominance + market cap momentum."""
    import requests
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=10,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        if not data:
            return 50.0

        # CoinGecko /global: BTC dominance is under market_cap_percentage.btc
        mcap_pct = data.get("market_cap_percentage", {}) or {}
        btc_dom = float(mcap_pct.get("btc", 50) or 50)
        mcap_chg_24h = float(data.get("market_cap_change_percentage_24h_usd", 0) or 0)

        # BTC dominance signal:
        # Rising BTC dom (>55%) = risk-off, capital fleeing to BTC = mildly bearish for alts
        # Falling BTC dom (<45%) = altseason / risk-on = bullish
        # For BTC itself, high dominance is actually neutral-bullish (BTC strength)
        # We use a balanced approach: extreme dominance either way = volatile
        if btc_dom > 60:        dom_score = 45.0   # Very high BTC dom = alts suppressed
        elif btc_dom > 55:      dom_score = 50.0
        elif btc_dom > 50:      dom_score = 55.0
        elif btc_dom > 45:      dom_score = 65.0   # Alt season territory = bullish
        else:                    dom_score = 70.0   # Very low BTC dom = full altseason

        # Market cap momentum (24h change)
        if mcap_chg_24h > 5:    mom_score = 80.0
        elif mcap_chg_24h > 2:  mom_score = 70.0
        elif mcap_chg_24h > 0:  mom_score = 58.0
        elif mcap_chg_24h > -2: mom_score = 45.0
        elif mcap_chg_24h > -5: mom_score = 32.0
        else:                    mom_score = 18.0

        combined = dom_score * 0.4 + mom_score * 0.6
        return float(max(0.0, min(100.0, combined)))
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

    scores["T10"] = safe(get_funding_oscillator_score_bybit, symbol)

    # ── On-Chain (O1-O7) ───────────────────────────────────────
    scores["O1"] = safe(get_mvrv_score, symbol, db)
    scores["O2"] = safe(get_netflow_score, symbol, db)
    scores["O3"] = safe(get_real_onchain_score, symbol, db) if symbol == "BTCUSDT" else 50.0
    scores["O4"] = safe(get_real_onchain_score, symbol, db) if symbol == "ETHUSDT" else 50.0
    scores["O5"] = safe(get_liquidation_score_bybit, symbol)
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
    scores["S4"] = safe(get_social_score_coingecko, symbol)
    scores["S5"] = safe(get_google_trends_score, symbol, db)
    scores["S6"] = safe(get_stocktwits_sentiment_score, symbol)

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
