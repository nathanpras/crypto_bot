# collector/macro_extended.py — Phase 7D: Extended Free API Collectors
"""
Four new free data sources:
1. Stablecoin flows     — DeFiLlama stablecoins API
2. FRED expansion       — CPI, 10Y yield, M2 velocity
3. Bybit basis          — futures premium/discount vs spot
4. DeFiLlama fees       — protocol fee revenue (ecosystem health)

All return normalized modifiers or structured dicts.
No API keys required except FRED (already optional in macro.py).
"""

import os
import requests
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

FRED_KEY          = os.getenv("FRED_API_KEY", "")
DEFILLAMA_BASE    = "https://stablecoins.llama.fi"
DEFILLAMA_FEES    = "https://api.llama.fi/overview/fees"
BYBIT_BASE        = "https://api.bytick.com/v5/market"
FRED_BASE         = "https://api.stlouisfed.org/fred/series/observations"


# ── 1. Stablecoin Flows ───────────────────────────────────────

def fetch_stablecoin_flows() -> dict:
    """
    Fetch total stablecoin market cap trend from DeFiLlama.
    Rising stablecoin supply = more dry powder = bullish.
    Returns {current_bn, change_7d_pct, change_30d_pct, modifier}.
    """
    try:
        r = requests.get(f"{DEFILLAMA_BASE}/stablecoins?includePrices=true",
                         timeout=15)
        r.raise_for_status()
        data = r.json()
        pegs = data.get("peggedAssets", [])

        # Sum USDT + USDC + DAI + BUSD (major stables)
        major = {"tether", "usd-coin", "dai", "binance-usd", "true-usd", "frax"}
        total_now = 0.0
        for p in pegs:
            if p.get("gecko_id", "").lower() in major or \
               p.get("name", "").lower() in {"tether", "usd coin", "dai", "busd"}:
                circulating = p.get("circulating", {})
                if isinstance(circulating, dict):
                    total_now += float(circulating.get("peggedUSD", 0) or 0)
                elif isinstance(circulating, (int, float)):
                    total_now += float(circulating)

        if total_now == 0:
            # fallback: sum ALL pegged assets
            for p in pegs:
                circ = p.get("circulating", {})
                if isinstance(circ, dict):
                    total_now += float(circ.get("peggedUSD", 0) or 0)

        total_bn = total_now / 1e9

        # Get 7d and 30d history from chain summary endpoint
        r2 = requests.get(f"{DEFILLAMA_BASE}/stablecoinchains", timeout=15)
        if r2.status_code == 200:
            chains = r2.json()
            # Sum all chains' total
            total_7d_ago  = sum(float(c.get("total7dAgo",  0) or 0) for c in chains)
            total_30d_ago = sum(float(c.get("total1mAgo",  0) or 0) for c in chains)
        else:
            total_7d_ago  = 0.0
            total_30d_ago = 0.0

        change_7d  = (total_now - total_7d_ago)  / total_7d_ago  * 100 \
                     if total_7d_ago  > 0 else 0.0
        change_30d = (total_now - total_30d_ago) / total_30d_ago * 100 \
                     if total_30d_ago > 0 else 0.0

        # Modifier: rising stablecoin supply = liquidity entering market
        if change_7d > 3.0:
            modifier = 5.0     # Strong inflow → bullish
        elif change_7d > 1.0:
            modifier = 2.0
        elif change_7d < -3.0:
            modifier = -5.0    # Strong outflow → bearish
        elif change_7d < -1.0:
            modifier = -2.0
        else:
            modifier = 0.0

        logger.debug(f"Stablecoin supply: ${total_bn:.1f}B | "
                     f"7d: {change_7d:+.1f}% | 30d: {change_30d:+.1f}%")
        return {
            "current_bn":    round(total_bn, 2),
            "change_7d_pct": round(change_7d, 2),
            "change_30d_pct": round(change_30d, 2),
            "modifier":      modifier,
            "status":        "ok",
        }

    except Exception as e:
        logger.warning(f"Stablecoin flow fetch failed: {e}")
        return {"status": "error", "modifier": 0.0, "current_bn": 0.0,
                "change_7d_pct": 0.0, "change_30d_pct": 0.0}


# ── 2. FRED Expansion (CPI + 10Y yield) ──────────────────────

def _fred_latest(series_id: str, limit: int = 4) -> list[float]:
    """Fetch last `limit` values from FRED. Returns [] if no key or error."""
    if not FRED_KEY or FRED_KEY == "your_fred_key_here":
        return []
    try:
        r = requests.get(FRED_BASE, params={
            "series_id":  series_id,
            "api_key":    FRED_KEY,
            "file_type":  "json",
            "sort_order": "desc",
            "limit":      limit,
        }, timeout=15)
        r.raise_for_status()
        obs = r.json().get("observations", [])
        vals = []
        for o in obs:
            try:
                vals.append(float(o["value"]))
            except (ValueError, KeyError):
                pass
        return vals
    except Exception as e:
        logger.debug(f"FRED {series_id} fetch failed: {e}")
        return []


def fetch_fred_extended() -> dict:
    """
    Fetch CPI (CPIAUCSL), 10Y yield (GS10), and M2 velocity (M2V).
    Returns structured dict with trend labels and F1 hint.
    """
    result = {}

    # CPI: falling = disinflationary = less rate pressure = bullish
    cpi_vals = _fred_latest("CPIAUCSL", 4)
    if len(cpi_vals) >= 2:
        cpi_trend = "falling" if cpi_vals[0] < cpi_vals[1] else \
                    "rising"  if cpi_vals[0] > cpi_vals[1] else "flat"
        result["cpi"] = {"latest": cpi_vals[0], "trend": cpi_trend}
    else:
        result["cpi"] = {"latest": None, "trend": "unknown"}

    # 10Y yield: rising yield = tightening = bearish for risk assets
    y10_vals = _fred_latest("GS10", 4)
    if len(y10_vals) >= 2:
        y10_trend = "rising"  if y10_vals[0] > y10_vals[1] else \
                    "falling" if y10_vals[0] < y10_vals[1] else "flat"
        result["yield_10y"] = {"latest": y10_vals[0], "trend": y10_trend}
    else:
        result["yield_10y"] = {"latest": None, "trend": "unknown"}

    # M2 velocity: declining velocity = money not circulating = neutral
    m2v_vals = _fred_latest("M2V", 2)
    if m2v_vals:
        result["m2_velocity"] = {"latest": m2v_vals[0]}
    else:
        result["m2_velocity"] = {"latest": None}

    # Composite F1 hint
    macro_favorable = (
        result["cpi"].get("trend") in ("falling", "flat")
        and result["yield_10y"].get("trend") in ("falling", "flat", "unknown")
    )
    result["macro_favorable"] = macro_favorable

    logger.debug(f"FRED extended: CPI={result['cpi']['trend']} | "
                 f"10Y={result['yield_10y']['trend']}")
    return result


# ── 3. Bybit Basis (spot vs futures) ─────────────────────────

def fetch_bybit_basis(symbols: list[str] = None) -> dict:
    """
    Fetch Bybit futures basis (premium/discount vs estimated spot).
    Basis = (futures_price - mark_price) / mark_price * 100
    Positive basis = futures trading at premium = bullish sentiment.
    Returns {symbol: {basis_pct, sentiment}} for each symbol.
    """
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    results = {}
    for symbol in symbols:
        try:
            r = requests.get(f"{BYBIT_BASE}/tickers",
                             params={"category": "linear", "symbol": symbol},
                             timeout=10)
            items = r.json().get("result", {}).get("list", [])
            if not items:
                continue
            t = items[0]
            last_price = float(t.get("lastPrice",  0) or 0)
            mark_price = float(t.get("markPrice",  0) or 0)
            index_price = float(t.get("indexPrice", 0) or 0)

            # Use index price as spot proxy
            spot = index_price if index_price > 0 else mark_price
            if spot == 0:
                continue

            basis_pct = (last_price - spot) / spot * 100

            if basis_pct > 0.5:
                sentiment = "bullish"
            elif basis_pct < -0.5:
                sentiment = "bearish"
            else:
                sentiment = "neutral"

            results[symbol] = {
                "basis_pct": round(basis_pct, 4),
                "sentiment": sentiment,
            }
        except Exception as e:
            logger.debug(f"Bybit basis fetch failed for {symbol}: {e}")

    logger.debug(f"Bybit basis: {results}")
    return results


def get_basis_modifier(symbol: str, basis_data: dict) -> float:
    """Return score modifier -5 to +5 from basis sentiment."""
    entry = basis_data.get(symbol, {})
    sentiment = entry.get("sentiment", "neutral")
    basis_pct = entry.get("basis_pct", 0.0)

    if sentiment == "bullish":
        return min(5.0, basis_pct * 3)    # scale by magnitude
    elif sentiment == "bearish":
        return max(-5.0, basis_pct * 3)
    return 0.0


# ── 4. DeFiLlama Fees ────────────────────────────────────────

def fetch_defillama_fees(top_n: int = 20) -> dict:
    """
    Fetch protocol fee revenue from DeFiLlama.
    High fees = protocols being used = ecosystem health.
    Returns {protocol_name: {daily_fees_usd, change_1d_pct}}.
    """
    try:
        r = requests.get(DEFILLAMA_FEES, timeout=15,
                         params={"excludeTotalDataChart": "true",
                                 "excludeTotalDataChartBreakdown": "true"})
        r.raise_for_status()
        protocols = r.json().get("protocols", [])

        result = {}
        for p in protocols[:top_n]:
            name       = p.get("name", "")
            daily      = float(p.get("total24h", 0) or 0)
            daily_prev = float(p.get("total48hto24h", 0) or 0)
            change     = ((daily - daily_prev) / daily_prev * 100
                          if daily_prev > 0 else 0.0)
            result[name] = {
                "daily_fees_usd": daily,
                "change_1d_pct":  round(change, 1),
            }

        logger.debug(f"DeFiLlama fees: {len(result)} protocols fetched")
        return result

    except Exception as e:
        logger.warning(f"DeFiLlama fees fetch failed: {e}")
        return {}


def get_fees_ecosystem_score(chain_slug: str, fees_data: dict) -> float:
    """
    Map a chain to its primary protocol and return ecosystem health modifier.
    Returns -3 to +3.
    """
    # Map chain slug → likely protocol name in DeFiLlama
    CHAIN_PROTOCOL_MAP = {
        "ethereum": "Uniswap",
        "solana":   "Jupiter",
        "bsc":      "PancakeSwap",
        "arbitrum": "GMX",
        "optimism": "Velodrome",
        "polygon":  "Uniswap V3",
        "avalanche": "Trader Joe",
    }
    protocol = CHAIN_PROTOCOL_MAP.get(chain_slug.lower())
    if not protocol or protocol not in fees_data:
        return 0.0

    change = fees_data[protocol].get("change_1d_pct", 0.0)
    if change > 20:
        return 3.0
    elif change > 5:
        return 1.5
    elif change < -20:
        return -3.0
    elif change < -5:
        return -1.5
    return 0.0


# ── Collect all Phase 7D data ─────────────────────────────────

def collect_all_extended(db=None) -> dict:
    """
    Fetch all Phase 7D data sources and return combined dict.
    Called once per hour in live mode.
    """
    stable = fetch_stablecoin_flows()
    fred   = fetch_fred_extended()
    basis  = fetch_bybit_basis()
    fees   = fetch_defillama_fees()

    result = {
        "stablecoin_flows": stable,
        "fred_extended":    fred,
        "bybit_basis":      basis,
        "defillama_fees":   fees,
    }

    logger.info(
        f"Phase 7D collected: "
        f"stable={stable.get('current_bn', 0):.1f}B "
        f"({stable.get('change_7d_pct', 0):+.1f}% 7d) | "
        f"CPI={fred.get('cpi', {}).get('trend', '?')} | "
        f"basis_coins={len(basis)}"
    )
    return result


DEFILLAMA_DEX = "https://api.llama.fi/overview/dexs"


def get_altseason_index(current_prices: dict) -> float:
    """
    M3: Altseason Index 0-100.
    Measures what % of top alts have a higher price than BTC in the dict.
    current_prices: {symbol: price_value} — all values on same scale.
    """
    btc_price = current_prices.get("BTCUSDT", 100.0)
    if btc_price <= 0:
        return 50.0

    alt_symbols = [s for s in current_prices if s != "BTCUSDT"]
    if not alt_symbols:
        return 50.0

    outperforming = sum(1 for s in alt_symbols if current_prices.get(s, 0) > btc_price)
    ratio = outperforming / len(alt_symbols)
    return max(0.0, min(100.0, ratio * 100))


def get_dex_cex_ratio_score() -> float:
    """
    M4: DEX/CEX volume ratio score 0-100.
    Rising DEX volume vs CEX = higher decentralization + DeFi momentum = bullish.
    """
    try:
        resp = requests.get(DEFILLAMA_DEX, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        dex_vol = float(data.get("total24h", 0) or 0)

        if dex_vol <= 0:
            return 50.0

        BTC_CEX_APPROX_DAILY = 50_000_000_000
        ratio = dex_vol / BTC_CEX_APPROX_DAILY

        if ratio > 0.20:    return 80.0
        elif ratio > 0.12:  return 68.0
        elif ratio > 0.08:  return 58.0
        elif ratio > 0.05:  return 50.0
        elif ratio > 0.03:  return 40.0
        else:                return 30.0

    except Exception as e:
        logger.debug(f"DEX/CEX ratio fetch failed: {e}")
        return 50.0
