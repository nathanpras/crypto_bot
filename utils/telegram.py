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
    return send(trade_text)


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


# ── Helper: plain-language interpretation ─────────────────────

def _fng_summary(fg_val: int) -> str:
    if fg_val <= 20:
        return f"😱 <b>Pasar sangat takut</b> ({fg_val}) — biasanya momen bagus untuk mulai beli"
    elif fg_val <= 40:
        return f"😨 <b>Pasar masih pesimis</b> ({fg_val}) — investor ragu-ragu"
    elif fg_val <= 55:
        return f"😐 <b>Pasar netral</b> ({fg_val}) — tidak ada arah yang jelas"
    elif fg_val <= 75:
        return f"😏 <b>Pasar mulai serakah</b> ({fg_val}) — hati-hati, risiko meningkat"
    else:
        return f"🤑 <b>Pasar terlalu serakah</b> ({fg_val}) — risiko tinggi untuk entry"


def _regime_label(regime: str) -> str:
    return {
        "TRENDING_BULL":  "Tren naik",
        "TRENDING_BEAR":  "Tren turun",
        "RANGING":        "Sideways (belum ada arah jelas)",
        "VOLATILE":       "Volatil (harga lagi liar)",
        "TRANSITIONING":  "Masa transisi",
    }.get(regime, regime)


def _coin_plain_verdict(r: dict, threshold: int) -> tuple[str, str]:
    """
    Returns (status_line, reason_line) in plain Indonesian.
    status_line  : e.g. "Belum masuk syarat (64.3/100)"
    reason_line  : e.g. "Tren bagus tapi volume belum konfirmasi. Butuh 5.7 poin lagi."
    """
    signals = r.get("signals", {})
    score   = r.get("total_score", 0)
    regime  = r.get("regime", "")
    gap     = threshold - score

    # Status
    if r.get("strong"):
        status = f"🌪 <b>PERFECT STORM!</b> ({score:.0f}/100)"
    elif r.get("fired"):
        status = f"🔔 <b>Signal masuk!</b> ({score:.0f}/100)"
    elif score >= 65:
        status = f"🟢 Hampir masuk syarat ({score:.0f}/100) — kurang {gap:.1f} poin"
    elif score >= 55:
        status = f"🟡 Mendekati syarat ({score:.0f}/100) — kurang {gap:.1f} poin"
    else:
        status = f"⬜ Belum siap ({score:.0f}/100) — kurang {gap:.1f} poin"

    # Reason: translate signal weaknesses/strengths to plain language
    good, bad = [], []

    if signals.get("trend_score", 50) >= 70:
        good.append("tren lagi bagus ke atas")
    elif signals.get("trend_score", 50) < 40:
        bad.append("tren masih turun" if regime == "TRENDING_BEAR" else "tren belum jelas")

    if signals.get("rsi_score", 50) >= 70:
        good.append("harga oversold (potensi bounce)")
    elif signals.get("rsi_score", 50) < 40:
        bad.append("momentum lemah")

    if signals.get("volume_score", 50) >= 70:
        good.append("volume besar mengkonfirmasi")
    elif signals.get("volume_score", 50) < 45:
        bad.append("volume masih sepi")

    if signals.get("wyckoff_score", 50) >= 65:
        good.append("pola akumulasi terbentuk")
    elif signals.get("wyckoff_score", 50) < 40:
        bad.append("belum ada pola akumulasi")

    if signals.get("onchain_score", 50) >= 70:
        good.append("pemain besar mulai masuk (onchain)")
    elif signals.get("onchain_score", 50) < 40:
        bad.append("onchain lemah")

    if signals.get("macd_score", 50) >= 75:
        good.append("momentum MACD kuat")
    elif signals.get("macd_score", 50) < 40:
        bad.append("MACD flat/bearish")

    if r.get("unlock_penalty", 0) >= 5:
        bad.append(f"ada token unlock besar (−{r['unlock_penalty']:.0f} poin)")
    if r.get("whale_modifier", 0) >= 3:
        good.append("whale lagi akumulasi")
    elif r.get("whale_modifier", 0) <= -3:
        bad.append("whale lagi jual")

    parts = []
    if good:
        parts.append("✅ " + ", ".join(good[:2]))
    if bad:
        parts.append("⚠️ " + ", ".join(bad[:2]))
    if not parts:
        parts.append("Data terbatas, skor netral")

    reason = "  " + " — ".join(parts)
    return status, reason


def send_scan_summary(results: list, macro: dict, f1: dict, f2: dict) -> bool:
    """Send a human-friendly scan summary in plain Indonesian."""
    from config import SIGNAL_THRESHOLD, SIGNAL_STRONG
    from datetime import datetime

    now    = datetime.utcnow().strftime("%H:%M UTC")
    fng    = macro.get("fear_greed", {})
    btcd   = macro.get("btc_dominance", 0)
    fg_val = fng.get("value", 50)

    active  = sorted([r for r in results if not r.get("blocked") and r.get("signals")],
                     key=lambda x: x.get("total_score", 0), reverse=True)
    blocked = sorted([r for r in results if r.get("blocked")],
                     key=lambda x: x.get("total_score", 0), reverse=True)
    fired   = [r for r in active if r.get("fired")]

    lines = [
        f"📊 <b>APEX — Laporan Scan {now}</b>",
        "══════════════════════════",
        "",
        "📰 <b>Kondisi Pasar Sekarang</b>",
        _fng_summary(fg_val),
    ]

    # BTC dominance context
    if btcd >= 62:
        lines.append(f"🔴 Bitcoin sangat dominan ({btcd:.1f}%) — hanya BTC yang boleh ditrade")
    elif btcd >= 56:
        lines.append(f"🟡 Bitcoin masih dominan ({btcd:.1f}%) — altcoin tier 3 dikunci dulu")
    else:
        lines.append(f"🟢 Altseason! Bitcoin melemah ({btcd:.1f}%) — semua coin bisa ditrade")

    # Fired signals
    if fired:
        lines += ["", "══════════════════════════",
                  "🔔 <b>SIGNAL MASUK — Cek untuk Trade!</b>"]
        for r in fired:
            sym   = r["symbol"].replace("USDT", "")
            price = r.get("price", 0)
            pstr  = f"${price:,.3f}" if price < 10 else (f"${price:,.2f}" if price < 1000 else f"${price:,.0f}")
            status, reason = _coin_plain_verdict(r, SIGNAL_THRESHOLD)
            lines += ["", f"<b>{sym}</b>  {pstr}  |  {_regime_label(r.get('regime',''))}",
                      status, reason]

    # Top active coins
    top_watch = [r for r in active if not r.get("fired")][:5]
    if top_watch:
        lines += ["", "══════════════════════════",
                  "🔍 <b>Coin yang Perlu Dipantau</b>"]
        for i, r in enumerate(top_watch, 1):
            sym   = r["symbol"].replace("USDT", "")
            price = r.get("price", 0)
            pstr  = f"${price:,.3f}" if price < 10 else (f"${price:,.2f}" if price < 1000 else f"${price:,.0f}")
            status, reason = _coin_plain_verdict(r, SIGNAL_THRESHOLD)
            lines += ["",
                      f"<b>{i}. {sym}</b>  {pstr}  |  {_regime_label(r.get('regime',''))}",
                      status, reason]

    # Blocked coins
    if blocked:
        btcd_unlock = 56.0 if btcd >= 56 else 50.0
        btcd_gap    = btcd - btcd_unlock
        lines += ["", "══════════════════════════",
                  "🔒 <b>Coin Dikunci — Menunggu Giliran</b>",
                  f"(Bitcoin perlu turun {btcd_gap:.1f}% lagi agar terbuka)",
                  ""]
        for r in blocked[:4]:
            sym   = r["symbol"].replace("USDT", "")
            score = r.get("total_score", 0)
            if score >= SIGNAL_THRESHOLD:
                tag  = "🔥"
                note = "langsung fired begitu terbuka!"
            elif score >= 65:
                tag  = "⚡"
                note = "hampir masuk syarat"
            else:
                tag  = "·"
                note = f"skor {score:.0f}/100"
            lines.append(f"  {tag} <b>{sym}</b> — {note}")

    # Simple action summary
    lines += ["", "══════════════════════════", "🎯 <b>Intinya:</b>"]
    if fired:
        lines.append("  Ada signal yang masuk — lihat notifikasi trade di atas.")
    elif blocked and blocked[0].get("total_score", 0) >= SIGNAL_THRESHOLD:
        btcd_unlock = 56.0 if btcd >= 56 else 50.0
        best_b = blocked[0]
        lines.append(
            f"  {best_b['symbol'].replace('USDT','')} punya skor {best_b['total_score']:.0f}/100 "
            f"tapi masih dikunci. Tunggu BTC.D turun ke {btcd_unlock:.0f}%."
        )
    elif active:
        best_a = active[0]
        sym = best_a["symbol"].replace("USDT", "")
        gap = SIGNAL_THRESHOLD - best_a["total_score"]
        lines.append(f"  Belum ada signal. {sym} paling dekat — kurang {gap:.1f} poin lagi.")
        lines.append("  Tidak perlu action apapun sekarang, tunggu scan berikutnya.")

    lines.append(f"\n<i>⏰ Scan otomatis berikutnya ~4 jam lagi</i>")
    return send("\n".join(lines))


def get_updates(offset: int = 0) -> list:
    """
    Poll Telegram getUpdates untuk command baru.
    Dipanggil dari asyncio via run_in_executor (blocking call).
    """
    if not TOKEN or TOKEN == "your_bot_token_here":
        return []
    try:
        r = requests.get(
            f"{BASE}/getUpdates",
            params={
                "offset":          offset,
                "timeout":         25,
                "allowed_updates": ["message"],
            },
            timeout=30,
        )
        data = r.json()
        if data.get("ok"):
            return data.get("result", [])
    except Exception as e:
        logger.error(f"getUpdates failed: {e}")
    return []
