# collector/token_unlocks.py
"""
Token unlock calendar.
Primary: DeFiLlama emission API (free, no Playwright required).
Fallback: Tokenomist.ai via Playwright (optional, skipped if not installed).
Graceful degradation: jika semua gagal, penalty = 0.
"""
import re
import time
import requests
from datetime import date, datetime, timedelta
from loguru import logger

from config import COINS, UNLOCK_PENALTIES, UNLOCK_LARGE_THRESHOLD
from database import get_db

DEFILLAMA_PROTOCOL_BASE = "https://api.llama.fi/protocol"


# Mapping symbol → slug di Tokenomist.ai
TOKENOMIST_SLUGS = {
    "SOLUSDT":  "solana",
    "XRPUSDT":  "xrp",
    "BNBUSDT":  "bnb",
    "ADAUSDT":  "cardano",
    "AVAXUSDT": "avalanche-2",
    "LINKUSDT": "chainlink",
    "DOTUSDT":  "polkadot",
    "TONUSDT":  "toncoin",
    "ONDOUSDT": "ondo-finance",
    "ARBUSDT":  "arbitrum",
    "OPUSDT":   "optimism",
    "NEARUSDT": "near",
    "INJUSDT":  "injective-protocol",
    "SUIUSDT":  "sui",
    "APTUSDT":  "aptos",
    "SEIUSDT":  "sei-network",
    "POLUSDT":  "polygon",
    # BTC dan ETH tidak punya scheduled token unlocks
}


def fetch_unlocks_defillama(symbol: str) -> list:
    """
    Fetch token unlock schedule from DeFiLlama protocol API.
    DeFiLlama includes vesting/unlock data in protocol detail responses.
    Returns list of unlock dicts (same schema as Tokenomist scraper).
    """
    slug = TOKENOMIST_SLUGS.get(symbol)
    if not slug:
        return []

    try:
        r = requests.get(f"{DEFILLAMA_PROTOCOL_BASE}/{slug}", timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()

        unlocks = []
        # DeFiLlama doesn't have a dedicated unlock endpoint but
        # "tokenBreakdowns" or "raises" can hint at vesting schedules.
        # We use "raises" + "unlocks" fields if present.
        raw_unlocks = data.get("unlocks", []) or []
        total_supply = float(data.get("totalSupply", 0) or 0)

        for u in raw_unlocks:
            try:
                unlock_ts   = u.get("timestamp") or u.get("date")
                unlock_pct  = float(u.get("noOfTokens", 0) or 0)
                if total_supply > 0:
                    unlock_pct = unlock_pct / total_supply * 100

                if isinstance(unlock_ts, (int, float)):
                    unlock_dt = date.fromtimestamp(unlock_ts)
                elif isinstance(unlock_ts, str):
                    unlock_dt = datetime.strptime(unlock_ts[:10], "%Y-%m-%d").date()
                else:
                    continue

                if unlock_dt < date.today():
                    continue

                unlocks.append({
                    "unlock_date":       unlock_dt,
                    "unlock_amount_usd": 0.0,   # DeFiLlama doesn't provide USD value here
                    "unlock_pct_supply": round(unlock_pct, 2),
                    "category":          u.get("type", "unknown"),
                })
            except Exception:
                continue

        if unlocks:
            logger.debug(f"DeFiLlama unlocks {symbol}: {len(unlocks)} events")
        return unlocks

    except Exception as e:
        logger.debug(f"DeFiLlama unlock fetch failed for {symbol}: {e}")
        return []


def scrape_tokenomist(symbol: str) -> list:
    """
    Scrape jadwal token unlock dari Tokenomist.ai menggunakan Playwright.
    Return list of unlock dicts. Return [] jika gagal (graceful degradation).
    """
    slug = TOKENOMIST_SLUGS.get(symbol)
    if not slug:
        return []

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page()

            url = f"https://tokenomist.ai/token/{slug}"
            page.goto(url, timeout=30_000, wait_until="networkidle")
            page.wait_for_timeout(3000)

            unlock_rows = page.query_selector_all(
                "[data-testid='unlock-row'], .unlock-event, tr.unlock"
            )

            results = []
            for row in unlock_rows[:10]:
                try:
                    text = row.inner_text()
                    date_match = re.search(
                        r'(\d{4}-\d{2}-\d{2})|([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})',
                        text
                    )
                    if not date_match:
                        continue

                    raw_date = date_match.group(0)
                    try:
                        if "-" in raw_date:
                            unlock_dt = datetime.strptime(raw_date, "%Y-%m-%d").date()
                        else:
                            unlock_dt = datetime.strptime(raw_date, "%b %d, %Y").date()
                    except ValueError:
                        continue

                    if unlock_dt < date.today():
                        continue

                    amount_match = re.search(r'\$?([\d,.]+)\s*([MB])', text)
                    amount_usd = 0.0
                    if amount_match:
                        num = float(amount_match.group(1).replace(",", ""))
                        mult = 1_000_000 if amount_match.group(2) == "M" else 1_000_000_000
                        amount_usd = num * mult

                    pct_match = re.search(r'([\d.]+)\s*%', text)
                    pct_supply = float(pct_match.group(1)) if pct_match else 0.0

                    category = "unknown"
                    for cat in ["team", "investor", "ecosystem", "community", "treasury"]:
                        if cat in text.lower():
                            category = cat
                            break

                    results.append({
                        "unlock_date":       unlock_dt,
                        "unlock_amount_usd": amount_usd,
                        "unlock_pct_supply": pct_supply,
                        "category":          category,
                    })

                except Exception:
                    continue

            browser.close()
            logger.debug(f"  {symbol}: found {len(results)} upcoming unlocks")
            return results

    except Exception as e:
        logger.warning(f"Tokenomist scrape failed for {symbol}: {e}")
        return []


def collect_all_token_unlocks():
    """
    Fetch token unlock calendar for all coins.
    Strategy: DeFiLlama API first (fast, no Playwright), Tokenomist fallback.
    Skip gracefully if all sources fail.
    """
    db = get_db()
    logger.info("Collecting token unlock calendar (DeFiLlama primary)...")

    for symbol in TOKENOMIST_SLUGS:
        if symbol not in COINS:
            continue
        try:
            # Try DeFiLlama first (no browser required)
            unlocks = fetch_unlocks_defillama(symbol)

            # Fallback to Playwright scraper if DeFiLlama returned nothing
            if not unlocks:
                unlocks = scrape_tokenomist(symbol)

            for unlock in unlocks:
                db.upsert_token_unlock(symbol, unlock)
            if unlocks:
                logger.info(f"  {symbol}: {len(unlocks)} unlock events saved")
            time.sleep(0.5)   # lighter delay since DeFiLlama is an API, not scraping
        except Exception as e:
            logger.warning(f"  {symbol} unlock collection failed: {e}")

    logger.info("Token unlock collection complete.")


def calc_unlock_penalty(unlocks: list) -> int:
    """
    Hitung total penalty berdasarkan upcoming unlock events.
    Ambil unlock terdekat dan terapkan tier penalty.
    Large supply (>= UNLOCK_LARGE_THRESHOLD %) menambah +10.
    """
    if not unlocks:
        return 0

    today = date.today()
    max_penalty = 0

    for unlock in unlocks:
        unlock_date = unlock.get("unlock_date")
        if isinstance(unlock_date, str):
            unlock_date = datetime.strptime(unlock_date, "%Y-%m-%d").date()

        if unlock_date is None or unlock_date < today:
            continue

        days_until = (unlock_date - today).days

        if days_until <= 7:
            base = UNLOCK_PENALTIES["days_7"]
        elif days_until <= 14:
            base = UNLOCK_PENALTIES["days_14"]
        elif days_until <= 30:
            base = UNLOCK_PENALTIES["days_30"]
        else:
            continue

        pct = unlock.get("unlock_pct_supply", 0) or 0
        if pct >= UNLOCK_LARGE_THRESHOLD:
            base += UNLOCK_PENALTIES["large_supply"]

        max_penalty = max(max_penalty, base)

    return max_penalty


def get_unlock_penalty(symbol: str, db=None) -> int:
    """
    Lookup upcoming unlock events dari DB dan hitung penalty.
    Return 0 jika tidak ada data (graceful fallback).
    """
    if db is None:
        db = get_db()

    unlocks = db.get_upcoming_unlocks(symbol, days=30)
    return calc_unlock_penalty(unlocks)
