# ============================================================
# collector/macro.py — Macro & Sentiment Data (F1 + F2 Gate)
# ============================================================

import requests
import os
from datetime import datetime, date
from loguru import logger
from dotenv import load_dotenv

from database import get_db

load_dotenv()

FRED_KEY    = os.getenv("FRED_API_KEY", "")
FNG_URL     = "https://api.alternative.me/fng/?limit=7"
COINLORE    = "https://api.coinlore.net/api/global/"


def fetch_fear_greed() -> dict:
    """
    Fetch Fear & Greed index from alternative.me (free, no key needed).
    Returns current value and 7-day average.
    """
    try:
        r = requests.get(FNG_URL, timeout=10)
        r.raise_for_status()
        data = r.json()["data"]
        current = int(data[0]["value"])
        avg_7d  = sum(int(d["value"]) for d in data) / len(data)

        label = data[0]["value_classification"]
        logger.debug(f"Fear & Greed: {current} ({label}) | 7d avg: {avg_7d:.0f}")
        return {
            "value": current,
            "label": label,
            "avg_7d": round(avg_7d, 1),
            "extreme_fear": current < 25,
            "extreme_greed": current > 75,
        }
    except Exception as e:
        logger.warning(f"Fear & Greed fetch failed: {e}")
        return {"value": 50, "label": "Neutral", "avg_7d": 50}


def fetch_btc_dominance() -> float:
    """
    Fetch BTC Dominance from CoinLore (free, no key needed).
    Critical for F2 Cycle Gate.
    """
    try:
        r = requests.get(COINLORE, timeout=10)
        r.raise_for_status()
        data = r.json()[0]
        dom  = float(data["btc_d"])
        logger.debug(f"BTC Dominance: {dom:.2f}%")
        return dom
    except Exception as e:
        logger.warning(f"BTC Dominance fetch failed: {e}")
        return 60.0  # Conservative default


def fetch_global_m2() -> dict:
    """
    Fetch Global M2 Money Supply from FRED (free API).
    Used for F1 Macro Gate — the most important filter.

    Series: WM2NS (M2 Money Stock, weekly, seasonally adjusted)
    Also fetches DXY (US Dollar Index) as confirmation.
    """
    if not FRED_KEY or FRED_KEY == "your_fred_key_here":
        logger.warning("FRED API key not set — M2 data unavailable")
        return {"status": "no_key", "trend": "unknown"}

    try:
        params = {
            "series_id":        "WM2NS",
            "api_key":          FRED_KEY,
            "file_type":        "json",
            "sort_order":       "desc",
            "limit":            12,  # Last 12 weeks
            "observation_start": "2024-01-01",
        }
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params=params, timeout=15
        )
        r.raise_for_status()
        obs = r.json()["observations"]

        # Parse values, skip missing
        values = []
        for o in obs:
            try:
                values.append(float(o["value"]))
            except (ValueError, KeyError):
                continue

        if len(values) < 4:
            return {"status": "insufficient_data", "trend": "unknown"}

        # Trend: compare latest 4 weeks vs 4 weeks before that
        recent = sum(values[:4]) / 4
        older  = sum(values[4:8]) / 4
        change = (recent - older) / older * 100

        trend = "expanding" if change > 0.1 else \
                "contracting" if change < -0.1 else "flat"

        logger.debug(f"Global M2: ${recent/1e6:.2f}T | trend: {trend} ({change:+.2f}%)")
        return {
            "status":  "ok",
            "current": recent,
            "change_pct_4w": round(change, 3),
            "trend":   trend,
            "f1_pass": trend in ("expanding", "flat"),  # Pass if not contracting
        }
    except Exception as e:
        logger.warning(f"FRED M2 fetch failed: {e}")
        return {"status": "error", "trend": "unknown", "f1_pass": None}


def evaluate_f1_gate(macro_data: dict) -> dict:
    """
    F1: Macro Gate — is global liquidity expanding?
    Returns pass/fail with explanation.
    """
    fng  = macro_data.get("fear_greed", {})
    m2   = macro_data.get("m2", {})
    btcd = macro_data.get("btc_dominance", 60)

    # F1 PASS conditions:
    # 1. M2 not contracting (most important)
    # 2. Fear & Greed not at extreme greed (overbought market)
    m2_ok  = m2.get("f1_pass", True)   # Default to pass if no key
    fng_ok = fng.get("value", 50) < 80  # Not extreme greed

    passed = m2_ok and fng_ok

    return {
        "passed":    passed,
        "m2_trend":  m2.get("trend", "unknown"),
        "fng_value": fng.get("value", 50),
        "fng_label": fng.get("label", "Neutral"),
        "reason":    (
            "PASS — Macro conditions acceptable" if passed else
            f"FAIL — {'M2 contracting' if not m2_ok else 'Market overbought (F&G>80)'}"
        ),
    }


def evaluate_f2_gate(btc_dominance: float) -> dict:
    """
    F2: Cycle Gate — which tiers are allowed?
    Based on BTC Dominance.
    """
    if btc_dominance < 50:
        allowed_tiers = [1, 2, 3]
        label = "FULL ALTSEASON 🟢"
    elif btc_dominance < 56:
        allowed_tiers = [1, 2, 3]
        label = "ALTSEASON (mild) 🟡"
    elif btc_dominance < 62:
        allowed_tiers = [1, 2]
        label = "PARTIAL (T1+T2 only) 🟡"
    else:
        allowed_tiers = [1]
        label = "BTC SEASON (T1 only) 🔴"

    return {
        "btc_dominance": btc_dominance,
        "allowed_tiers": allowed_tiers,
        "label":         label,
    }


def fetch_all_macro() -> dict:
    """Fetch all macro data and run F1+F2 gates. Call weekly."""
    logger.info("Fetching macro data (F1 + F2 gates)...")

    fng  = fetch_fear_greed()
    btcd = fetch_btc_dominance()
    m2   = fetch_global_m2()

    macro = {"fear_greed": fng, "m2": m2, "btc_dominance": btcd}

    f1 = evaluate_f1_gate(macro)
    f2 = evaluate_f2_gate(btcd)

    result = {
        "macro":    macro,
        "f1_gate":  f1,
        "f2_gate":  f2,
        "fetched_at": datetime.utcnow().isoformat(),
    }

    # Store to database
    db = get_db()
    db.upsert_macro(date.today(), {
        "btc_dominance": btcd,
        "fear_greed":    fng["value"],
        "global_m2":     m2.get("current"),
        "dxy":           None,  # Add DXY later
    })

    logger.info(f"F1 Gate: {f1['reason']}")
    logger.info(f"F2 Gate: {f2['label']} | Tiers allowed: {f2['allowed_tiers']}")

    return result


if __name__ == "__main__":
    result = fetch_all_macro()
    print("\n── Macro Summary ──")
    print(f"F&G: {result['macro']['fear_greed']['value']} "
          f"({result['macro']['fear_greed']['label']})")
    print(f"BTC.D: {result['macro']['btc_dominance']:.1f}%")
    print(f"M2 trend: {result['macro']['m2'].get('trend','unknown')}")
    print(f"F1 Gate: {result['f1_gate']['reason']}")
    print(f"F2 Gate: {result['f2_gate']['label']}")
