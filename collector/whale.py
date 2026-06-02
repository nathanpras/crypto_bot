# collector/whale.py
from loguru import logger
from config import WHALE_SCORING


def calc_whale_score(symbol: str, db) -> float:
    """
    Whale accumulation/distribution score.
    BTC/ETH: exchange netflow 7d dari tabel onchain.
    Altcoin: OI anomaly dari tabel futures_metrics.
    Return: -10 to +8.
    """
    cfg = WHALE_SCORING

    if symbol == "BTCUSDT":
        netflow = db.get_whale_netflow_7d("BTCUSDT")
        if netflow is None:
            return 0.0
        if netflow < cfg["btc_outflow_bullish"]:
            return 8.0
        if netflow < 0:
            return 4.0
        if netflow > cfg["btc_inflow_bearish"]:
            return -10.0
        if netflow > 0:
            return -4.0
        return 0.0

    if symbol == "ETHUSDT":
        netflow = db.get_whale_netflow_7d("ETHUSDT")
        if netflow is None:
            return 0.0
        if netflow < cfg["eth_outflow_bullish"]:
            return 8.0
        if netflow < 0:
            return 4.0
        if netflow > cfg["eth_inflow_bearish"]:
            return -10.0
        if netflow > 0:
            return -4.0
        return 0.0

    # Altcoins: OI anomaly
    oi_data = db.get_oi_change_7d(symbol)
    if not oi_data:
        return 0.0

    oi_chg  = oi_data["oi_change_pct"]
    funding = oi_data["avg_funding"]

    if oi_chg >= cfg["oi_surge_bullish"] and abs(funding) <= cfg["funding_neutral_max"]:
        return 5.0
    if oi_chg <= cfg["oi_drop_bearish"]:
        return -5.0
    return 0.0


def get_whale_modifier(symbol: str, db) -> float:
    """Return whale modifier for engine.py. Never raises."""
    try:
        return calc_whale_score(symbol, db)
    except Exception as e:
        logger.error(f"Whale modifier error for {symbol}: {e}")
        return 0.0
