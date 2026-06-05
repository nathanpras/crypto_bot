# collector/onchain_real.py
import os
import requests
from datetime import datetime, timedelta, date as date_type
from loguru import logger

BLOCKCHAIN_INFO_STATS = "https://api.blockchain.info/stats"
BLOCKCHAIN_INFO_TX_VOL = "https://api.blockchain.info/charts/estimated-transaction-volume-usd"
ETHERSCAN_BASE = "https://api.etherscan.io/api"


def fetch_btc_onchain() -> dict:
    """Fetch BTC on-chain data from Blockchain.info (no key needed)."""
    try:
        stats_resp = requests.get(BLOCKCHAIN_INFO_STATS, timeout=15)
        stats_resp.raise_for_status()
        stats = stats_resp.json()

        active_addr = int(stats.get("n_unique_addresses", 0))
        tx_count = int(stats.get("n_tx", 0))

        vol_resp = requests.get(
            BLOCKCHAIN_INFO_TX_VOL,
            params={"timespan": "5days", "format": "json"},
            timeout=15,
        )
        vol_resp.raise_for_status()

        return {
            "asset": "BTC",
            "date": datetime.utcnow().date(),
            "active_addr": active_addr,
            "tx_count": tx_count,
            "exchange_inflow": 0.0,
            "exchange_outflow": 0.0,
            "nvt_ratio": 0.0,
        }
    except Exception as e:
        logger.debug(f"Blockchain.info fetch failed: {e}")
        return {}


def fetch_eth_onchain() -> dict:
    """Fetch ETH on-chain data from Etherscan (free key required)."""
    api_key = os.getenv("ETHERSCAN_API_KEY")
    if not api_key:
        logger.debug("ETHERSCAN_API_KEY not set — skipping ETH on-chain")
        return {}

    try:
        today = datetime.utcnow()
        yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        today_str = today.strftime("%Y-%m-%d")

        resp = requests.get(
            ETHERSCAN_BASE,
            params={
                "module": "stats",
                "action": "dailytx",
                "startdate": yesterday,
                "enddate": today_str,
                "sort": "desc",
                "apikey": api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        tx_count = 0
        if data.get("status") == "1" and data.get("result"):
            tx_count = int(data["result"][0].get("uniqTxsCount", 0))

        return {
            "asset": "ETH",
            "date": datetime.utcnow().date(),
            "active_addr": tx_count,
            "tx_count": tx_count,
            "exchange_inflow": 0.0,
            "exchange_outflow": 0.0,
            "nvt_ratio": 0.0,
        }
    except Exception as e:
        logger.debug(f"Etherscan fetch failed: {e}")
        return {}


def compute_nvt_score(asset: str, db) -> float:
    """
    NVT proxy: compare recent 7d tx activity vs prior 7d.
    Rising activity = bullish (lower NVT = cheaper relative to usage).
    Returns 0-100.
    """
    rows = db.get_onchain_real_history(asset, days=14)
    if len(rows) < 7:
        return 50.0

    recent = [r["tx_count"] for r in rows[:7] if r["tx_count"]]
    older = [r["tx_count"] for r in rows[7:14] if r["tx_count"]]

    if not recent or not older:
        return 50.0

    avg_recent = sum(recent) / len(recent)
    avg_older = sum(older) / len(older)

    if avg_older == 0:
        return 50.0

    ratio = avg_recent / avg_older
    if ratio > 1.4:    return 80.0
    elif ratio > 1.2:  return 68.0
    elif ratio > 1.05: return 58.0
    elif ratio > 0.95: return 50.0
    elif ratio > 0.8:  return 40.0
    elif ratio > 0.6:  return 30.0
    else:               return 20.0


def get_real_onchain_score(symbol: str, db) -> float:
    """
    O3/O4: Score 0-100 using real on-chain activity.
    Returns 50.0 for non-BTC/ETH or stale data.
    """
    asset_map = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}
    asset = asset_map.get(symbol)
    if not asset:
        return 50.0
    return compute_nvt_score(asset, db)


def collect_all_onchain_real(db) -> dict:
    """Fetch BTC + ETH real on-chain data and store in DB."""
    results = {}
    for fetch_fn, asset in [(fetch_btc_onchain, "BTC"), (fetch_eth_onchain, "ETH")]:
        data = fetch_fn()
        if data:
            try:
                db.upsert_onchain_real(asset, data)
                results[asset] = "ok"
                logger.info(f"On-chain real collected: {asset} — {data.get('tx_count', 0):,} tx")
            except Exception as e:
                logger.warning(f"Failed to store on-chain real for {asset}: {e}")
                results[asset] = "error"
        else:
            results[asset] = "skipped"
    return results
