# ============================================================
# utils/telegram.py — Telegram Alert Bot
# ============================================================

import os
import requests
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BASE    = f"https://api.telegram.org/bot{TOKEN}"


def send(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to your Telegram chat."""
    if not TOKEN or TOKEN == "your_bot_token_here":
        logger.warning("Telegram not configured — printing to console instead")
        print("\n" + "═"*40)
        print(text)
        print("═"*40 + "\n")
        return False
    try:
        r = requests.post(f"{BASE}/sendMessage", json={
            "chat_id":    CHAT_ID,
            "text":       text,
            "parse_mode": parse_mode,
        }, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def send_signal_alert(trade_text: str) -> bool:
    return send(f"<pre>{trade_text}</pre>")


def send_daily_report(stats: dict, macro: dict) -> bool:
    fng  = macro.get("macro", {}).get("fear_greed", {})
    f2   = macro.get("f2_gate", {})

    text = f"""
📊 <b>APEX Daily Report</b>
{'─'*30}
<b>Market</b>
F&G: {fng.get('value','—')} ({fng.get('label','—')})
BTC.D: {macro.get('macro',{}).get('btc_dominance','—'):.1f}%
Tiers: {f2.get('allowed_tiers','—')}

<b>Today's Trades</b>
Total: {stats.get('total',0)} | Wins: {stats.get('wins',0)}
Win rate: {stats.get('win_rate',0)}%
Total P&L: ${stats.get('total_pnl',0):.2f}
Avg R: {stats.get('avg_r',0):.2f}R

<b>Best:</b> ${stats.get('best',0):.2f}
<b>Worst:</b> ${stats.get('worst',0):.2f}
    """.strip()
    return send(text)


def send_system_status(status: str, details: str = "") -> bool:
    icons = {"started": "🟢", "stopped": "🔴", "warning": "⚠️", "error": "🚨"}
    icon  = icons.get(status, "ℹ️")
    text  = f"{icon} <b>APEX System {status.upper()}</b>"
    if details:
        text += f"\n<code>{details}</code>"
    return send(text)


def send_error(error: str) -> bool:
    return send(f"🚨 <b>APEX Error</b>\n<code>{error}</code>")
