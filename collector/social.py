# collector/social.py
import time
import requests
from loguru import logger
from config import COINGECKO_MAP, SOCIAL_SCORING

BASE_URL = "https://api.coingecko.com/api/v3/coins"


def fetch_social_data(coingecko_id: str):
    """Ambil community + developer data dari CoinGecko free API."""
    try:
        r = requests.get(
            f"{BASE_URL}/{coingecko_id}",
            params={
                "localization":   "false",
                "tickers":        "false",
                "market_data":    "false",
                "community_data": "true",
                "developer_data": "true",
                "sparkline":      "false",
            },
            timeout=15,
            headers={"Accept": "application/json"},
        )
        if r.status_code == 429:
            logger.warning("CoinGecko rate limit, sleeping 60s")
            time.sleep(60)
            return None
        data = r.json()
        comm = data.get("community_data", {})
        dev  = data.get("developer_data", {})
        return {
            "twitter_followers":  comm.get("twitter_followers", 0) or 0,
            "reddit_subscribers": comm.get("reddit_subscribers", 0) or 0,
            "telegram_members":   comm.get("telegram_channel_user_count", 0) or 0,
            "github_commits_4w":  dev.get("commit_count_4_weeks", 0) or 0,
        }
    except Exception as e:
        logger.error(f"CoinGecko fetch failed for {coingecko_id}: {e}")
        return None


def calc_social_score(twitter_change_30d: float,
                      reddit_change_30d: float,
                      github_commits_4w: int) -> float:
    """Return social modifier: -5 to +8."""
    score = 0.0
    cfg   = SOCIAL_SCORING

    if twitter_change_30d >= cfg["twitter_growth_strong"]["threshold"]:
        score += cfg["twitter_growth_strong"]["modifier"]
    elif twitter_change_30d <= cfg["twitter_decline"]["threshold"]:
        score += cfg["twitter_decline"]["modifier"]

    if reddit_change_30d >= cfg["reddit_growth_strong"]["threshold"]:
        score += cfg["reddit_growth_strong"]["modifier"]

    if github_commits_4w >= cfg["github_active"]["threshold"]:
        score += cfg["github_active"]["modifier"]

    if (twitter_change_30d < 0 and reddit_change_30d < 0
            and github_commits_4w == 0):
        score += cfg["all_declining"]["modifier"]

    return max(-5.0, min(8.0, score))


def get_social_modifier(symbol: str, db) -> float:
    """Return social score modifier. 0 jika belum ada data."""
    metrics = db.get_latest_social(symbol)
    if not metrics:
        return 0.0
    return float(metrics.get("social_score", 0.0))


def collect_all_social(db) -> None:
    """Fetch social metrics untuk semua 19 coin. Dipanggil sekali sehari."""
    for symbol, cg_id in COINGECKO_MAP.items():
        data = fetch_social_data(cg_id)
        if not data:
            time.sleep(2)
            continue

        prev    = db.get_latest_social(symbol)
        prev_tw = prev["twitter_followers"] if prev else data["twitter_followers"]
        prev_rd = prev["reddit_subscribers"] if prev else data["reddit_subscribers"]

        tw_change = ((data["twitter_followers"] - prev_tw) / prev_tw * 100
                     if prev_tw and prev_tw > 0 else 0.0)
        rd_change = ((data["reddit_subscribers"] - prev_rd) / prev_rd * 100
                     if prev_rd and prev_rd > 0 else 0.0)

        score = calc_social_score(tw_change, rd_change, data["github_commits_4w"])

        db.upsert_social_metrics(symbol, {
            **data,
            "twitter_change_30d": round(tw_change, 2),
            "reddit_change_30d":  round(rd_change, 2),
            "social_score":       score,
        })
        logger.info(f"Social {symbol}: tw={tw_change:+.1f}% gh={data['github_commits_4w']} score={score:+.1f}")
        time.sleep(2)
