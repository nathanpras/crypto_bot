# collector/orderbook.py
import requests
from loguru import logger

BYBIT_ORDERBOOK_URL = "https://api.bytick.com/v5/market/orderbook"


def fetch_orderbook_imbalance(symbol: str, depth: int = 25) -> float:
    """
    Fetch Bybit L2 orderbook and compute bid/ask volume imbalance ratio.
    Returns bid_vol / ask_vol. >1 = bid-heavy (bullish), <1 = ask-heavy (bearish).
    Returns 1.0 on failure (neutral).
    """
    try:
        resp = requests.get(
            BYBIT_ORDERBOOK_URL,
            params={"category": "linear", "symbol": symbol, "limit": depth},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            return 1.0

        result = data.get("result", {})
        bids = result.get("b", [])
        asks = result.get("a", [])

        if not bids or not asks:
            return 1.0

        bid_vol = sum(float(b[1]) for b in bids[:10])
        ask_vol = sum(float(a[1]) for a in asks[:10])

        if ask_vol == 0:
            return 1.0
        return bid_vol / ask_vol

    except Exception as e:
        logger.debug(f"Orderbook fetch failed for {symbol}: {e}")
        return 1.0


def get_orderbook_score(symbol: str, imbalance_ratio: float) -> float:
    """
    T9: Score 0-100 from bid/ask imbalance ratio.
    ratio > 1.5 -> 80+, ratio < 0.67 -> 20-, ratio = 1.0 -> 50.
    """
    if imbalance_ratio >= 2.0:    return 90.0
    elif imbalance_ratio >= 1.5:  return 78.0
    elif imbalance_ratio >= 1.2:  return 63.0
    elif imbalance_ratio >= 0.95: return 50.0
    elif imbalance_ratio >= 0.8:  return 38.0
    elif imbalance_ratio >= 0.6:  return 25.0
    else:                          return 12.0
