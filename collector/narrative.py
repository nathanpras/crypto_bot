# collector/narrative.py
"""
DeFiLlama TVL data collection and sector rotation scoring.
API gratis, tidak butuh API key.
"""
import requests
import time
from loguru import logger

from config import COINS, SECTOR_MAP, NARRATIVE_THRESHOLDS, NARRATIVE_MODIFIERS
from database import get_db


DEFILLAMA_BASE = "https://api.llama.fi"


def fetch_chain_tvl(chain: str) -> dict:
    """
    Fetch TVL historis untuk satu chain dari DeFiLlama.
    Return dict dengan tvl_usd, tvl_change_7d, tvl_change_30d.
    """
    try:
        r = requests.get(
            f"{DEFILLAMA_BASE}/v2/historicalChainTvl/{chain}",
            timeout=15
        )
        r.raise_for_status()
        data = r.json()

        if not data or len(data) < 31:
            logger.debug(f"Insufficient TVL history for {chain}: {len(data)} points")
            return {}

        current  = float(data[-1]["tvl"])
        week_ago = float(data[-7]["tvl"])   if len(data) >= 7  else current
        month_ago= float(data[-30]["tvl"])  if len(data) >= 30 else current

        change_7d  = (current - week_ago)  / week_ago  * 100 if week_ago  > 0 else 0
        change_30d = (current - month_ago) / month_ago * 100 if month_ago > 0 else 0

        logger.debug(f"TVL {chain}: ${current/1e9:.2f}B | "
                     f"7d: {change_7d:+.1f}% | 30d: {change_30d:+.1f}%")

        return {
            "tvl_usd":        current,
            "tvl_change_7d":  round(change_7d, 2),
            "tvl_change_30d": round(change_30d, 2),
        }

    except Exception as e:
        logger.warning(f"DeFiLlama TVL fetch failed for {chain}: {e}")
        return {}


def collect_all_tvl():
    """Fetch TVL untuk semua sektor unik di SECTOR_MAP. Simpan ke DB."""
    db = get_db()
    sectors_done = set()

    logger.info("Collecting DeFiLlama TVL data...")

    for symbol, chain in SECTOR_MAP.items():
        if chain in sectors_done:
            continue
        sectors_done.add(chain)

        try:
            tvl_data = fetch_chain_tvl(chain)
            if tvl_data:
                db.upsert_sector_tvl(chain, tvl_data)
                logger.debug(f"  {chain}: 30d change = {tvl_data['tvl_change_30d']:+.1f}%")
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"  Failed TVL for {chain}: {e}")

    logger.info(f"TVL collection complete. {len(sectors_done)} sectors updated.")


def calc_sector_modifier(tvl_change_30d: float) -> tuple:
    """
    Hitung score modifier berdasarkan TVL 30d change.
    Return (modifier_int, label_str).
    """
    t = NARRATIVE_THRESHOLDS
    m = NARRATIVE_MODIFIERS

    if tvl_change_30d >= t["strong_up"]:
        return m["strong_up"], f"strong_up TVL {tvl_change_30d:+.1f}%"
    elif tvl_change_30d >= t["mild_up"]:
        return m["mild_up"], f"mild_up TVL {tvl_change_30d:+.1f}%"
    elif tvl_change_30d <= t["strong_down"]:
        return m["strong_down"], f"strong_down TVL {tvl_change_30d:+.1f}%"
    elif tvl_change_30d <= t["mild_down"]:
        return m["mild_down"], f"mild_down TVL {tvl_change_30d:+.1f}%"
    else:
        return m["neutral"], f"neutral TVL {tvl_change_30d:+.1f}%"


def get_sector_modifier(symbol: str, db=None) -> int:
    """
    Lookup TVL modifier untuk satu coin dari DB.
    Return 0 jika tidak ada data (graceful fallback — jangan block trade).
    """
    if db is None:
        db = get_db()

    chain = SECTOR_MAP.get(symbol)
    if not chain:
        return 0

    tvl_data = db.get_sector_tvl(chain)
    if not tvl_data:
        return 0

    change_30d = tvl_data.get("tvl_change_30d", 0) or 0
    modifier, _ = calc_sector_modifier(change_30d)
    return modifier
