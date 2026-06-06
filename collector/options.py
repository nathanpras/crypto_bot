# collector/options.py
import requests
from datetime import datetime
from loguru import logger

from config import OPTIONS_SCORING

DERIBIT_URL    = "https://www.deribit.com/api/v2/public"
OPTIONS_SYMBOLS = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}


def fetch_options_data(currency: str):
    """
    Ambil data options dari Deribit public API (gratis, tanpa key).
    Return dict atau None jika gagal.
    """
    try:
        r = requests.get(
            f"{DERIBIT_URL}/get_book_summary_by_currency",
            params={"currency": currency, "kind": "option"},
            timeout=15,
        )
        data = r.json().get("result", [])
        if not data:
            return None

        put_vol  = sum(d.get("volume", 0) for d in data if "P" in d.get("instrument_name", ""))
        call_vol = sum(d.get("volume", 0) for d in data if "C" in d.get("instrument_name", ""))
        pc_ratio = (put_vol / call_vol) if call_vol > 0 else 1.0

        put_ivs  = [d.get("mark_iv", 0) for d in data if "P" in d.get("instrument_name", "") and d.get("mark_iv")]
        call_ivs = [d.get("mark_iv", 0) for d in data if "C" in d.get("instrument_name", "") and d.get("mark_iv")]
        skew     = (sum(put_ivs) / len(put_ivs) - sum(call_ivs) / len(call_ivs)) if put_ivs and call_ivs else 0.0

        all_ivs = [d.get("mark_iv", 0) for d in data if d.get("mark_iv")]
        iv_atm  = sum(all_ivs) / len(all_ivs) if all_ivs else 0.0

        return {
            "put_call_ratio": round(pc_ratio, 3),
            "skew_25d":       round(skew, 3),
            "iv_atm":         round(iv_atm / 100, 4),
        }
    except Exception as e:
        logger.error(f"Deribit fetch failed for {currency}: {e}")
        return None


def calc_options_modifier(put_call_ratio: float, skew_25d: float) -> float:
    """Hitung score modifier dari put/call ratio dan skew."""
    cfg = OPTIONS_SCORING
    if put_call_ratio < cfg["strong_bullish"]["pc_max"] and skew_25d < cfg["strong_bullish"]["skew_max"]:
        return float(cfg["strong_bullish"]["modifier"])
    if put_call_ratio < cfg["mild_bullish"]["pc_max"]:
        return float(cfg["mild_bullish"]["modifier"])
    if put_call_ratio > cfg["strong_bearish"]["pc_min"] and skew_25d > cfg["strong_bearish"]["skew_min"]:
        return float(cfg["strong_bearish"]["modifier"])
    if put_call_ratio > cfg["mild_bearish"]["pc_max"]:
        return float(cfg["mild_bearish"]["modifier"])
    return 0.0


def get_options_modifier(symbol: str, db) -> float:
    """Return options modifier. 0.0 untuk altcoin selain BTC/ETH."""
    if symbol not in OPTIONS_SYMBOLS:
        return 0.0
    metrics = db.get_latest_options(symbol)
    if not metrics:
        # Fallback: fetch live dari Deribit kalau DB kosong
        currency = OPTIONS_SYMBOLS[symbol]
        data = fetch_options_data(currency)
        if data:
            try:
                db.upsert_options_metrics(symbol, data)
            except Exception:
                pass
            metrics = data
        else:
            return 0.0
    return calc_options_modifier(
        metrics.get("put_call_ratio") or 1.0,
        metrics.get("skew_25d") or 0.0,
    )


def collect_all_options(db) -> None:
    """Fetch dan simpan options metrics untuk BTC + ETH."""
    for symbol, currency in OPTIONS_SYMBOLS.items():
        data = fetch_options_data(currency)
        if data:
            db.upsert_options_metrics(symbol, data)
            logger.info(f"Options {symbol}: P/C={data['put_call_ratio']:.2f} skew={data['skew_25d']:.2f}")
        else:
            logger.warning(f"Options fetch failed for {symbol}")
