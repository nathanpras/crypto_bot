# APEX Phase 6 — Adaptive Regime Weights + Kill Zone Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** (1) Gunakan bobot sinyal berbeda per market regime — trending/ranging/volatile/bear. (2) Tambah +5 modifier saat sinyal fire di kill zone (London/NY session open).

**Architecture:** Perubahan murni di `config.py` dan `signals/engine.py`. Tidak ada collector baru. `detect_regime()` dipindah ke SEBELUM weighted total sehingga bisa dipakai untuk pilih bobot yang tepat.

**Tech Stack:** Python 3.11 — hanya modifikasi kode existing.

---

## File Structure

```
config.py           [MODIFY] +REGIME_WEIGHTS dict, +KILL_ZONE_BONUS
signals/engine.py   [MODIFY] pindah regime detection, adaptive weights, kill zone modifier
tests/test_phase6.py[CREATE] 6 unit tests
```

---

## Task 1: Config Changes + Tests

**Files:** `config.py`, `tests/test_phase6.py`

- [ ] **Step 1: Append ke config.py** (setelah SIGNAL_WEIGHTS block):

```python
# ─── Phase 6: Regime-Adaptive Signal Weights ─────────────────
# Setiap regime punya bobot berbeda. Total harus = 1.0.
REGIME_WEIGHTS = {
    "TRENDING_BULL": {
        "trend_alignment":  0.30,   # trend lebih reliable saat trending
        "rsi_momentum":     0.12,
        "macd_momentum":    0.10,
        "volume_confirm":   0.15,
        "wyckoff_phase":    0.10,
        "onchain_signal":   0.13,
        "sentiment_score":  0.10,
    },
    "TRENDING_BEAR": {
        "trend_alignment":  0.25,
        "rsi_momentum":     0.10,
        "macd_momentum":    0.08,
        "volume_confirm":   0.12,
        "wyckoff_phase":    0.10,
        "onchain_signal":   0.20,   # on-chain lebih penting di bear
        "sentiment_score":  0.15,   # sentiment lebih penting di bear
    },
    "RANGING": {
        "trend_alignment":  0.10,   # trend tidak reliable saat ranging
        "rsi_momentum":     0.25,   # RSI terbaik di ranging
        "macd_momentum":    0.12,
        "volume_confirm":   0.15,
        "wyckoff_phase":    0.22,   # Wyckoff sangat berguna di ranging
        "onchain_signal":   0.10,
        "sentiment_score":  0.06,
    },
    "VOLATILE": {
        "trend_alignment":  0.15,
        "rsi_momentum":     0.15,
        "macd_momentum":    0.10,
        "volume_confirm":   0.20,   # volume paling penting saat volatile
        "wyckoff_phase":    0.10,
        "onchain_signal":   0.20,   # on-chain penting untuk konfirmasi
        "sentiment_score":  0.10,
    },
    "TRANSITIONING": {
        "trend_alignment":  0.18,
        "rsi_momentum":     0.18,
        "macd_momentum":    0.12,
        "volume_confirm":   0.17,
        "wyckoff_phase":    0.15,
        "onchain_signal":   0.12,
        "sentiment_score":  0.08,
    },
}

# ─── Phase 6: Kill Zone Settings ─────────────────────────────
KILL_ZONE_BONUS = 5   # +5 saat sinyal fire di London/NY session open
```

- [ ] **Step 2: Verifikasi semua weights sum = 1.0:**

```bash
py -c "
from config import REGIME_WEIGHTS
for regime, w in REGIME_WEIGHTS.items():
    total = sum(w.values())
    status = '✅' if abs(total - 1.0) < 0.001 else '❌'
    print(f'{status} {regime}: {total:.3f}')
"
```

Expected: semua ✅ 1.000

- [ ] **Step 3: Buat tests/test_phase6.py:**

```python
import pytest
import sys
from pathlib import Path
from unittest.mock import patch
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import REGIME_WEIGHTS, SIGNAL_WEIGHTS, KILL_ZONE_BONUS, KILL_ZONES_UTC
from signals.engine import get_kill_zone_modifier, get_regime_weights


def test_regime_weights_all_sum_to_one():
    """Semua regime weights harus sum = 1.0."""
    for regime, weights in REGIME_WEIGHTS.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.001, f"{regime} weights sum = {total}"


def test_get_regime_weights_trending_bull():
    """TRENDING_BULL harus return bobot trend lebih tinggi dari default."""
    w = get_regime_weights("TRENDING_BULL")
    w_default = SIGNAL_WEIGHTS["trend_alignment"]
    assert w["trend_alignment"] > w_default


def test_get_regime_weights_ranging():
    """RANGING harus return bobot RSI lebih tinggi dari TRENDING_BULL."""
    w_ranging  = get_regime_weights("RANGING")
    w_trending = get_regime_weights("TRENDING_BULL")
    assert w_ranging["rsi_momentum"] > w_trending["rsi_momentum"]


def test_get_regime_weights_fallback():
    """Regime tidak dikenal harus fallback ke SIGNAL_WEIGHTS."""
    w = get_regime_weights("UNKNOWN_REGIME")
    assert w == SIGNAL_WEIGHTS


def test_kill_zone_modifier_inside():
    """Saat jam London open (08:00 UTC), harus return +KILL_ZONE_BONUS."""
    fake_time = datetime(2026, 6, 2, 8, 0, 0)   # 08:00 UTC = London open
    with patch("signals.engine.datetime") as mock_dt:
        mock_dt.utcnow.return_value = fake_time
        in_zone, modifier = get_kill_zone_modifier()
    assert in_zone is True
    assert modifier == KILL_ZONE_BONUS


def test_kill_zone_modifier_outside():
    """Saat jam 05:00 UTC (di luar kill zone), harus return 0."""
    fake_time = datetime(2026, 6, 2, 5, 0, 0)
    with patch("signals.engine.datetime") as mock_dt:
        mock_dt.utcnow.return_value = fake_time
        in_zone, modifier = get_kill_zone_modifier()
    assert in_zone is False
    assert modifier == 0
```

- [ ] **Step 4: Jalankan — FAIL (ImportError get_kill_zone_modifier):**

```
py -m pytest tests/test_phase6.py -v 2>&1 | head -15
```

- [ ] **Step 5: Commit config:**

```
git add config.py tests/test_phase6.py
git commit -m "feat: Phase 6 config — REGIME_WEIGHTS + KILL_ZONE_BONUS"
```

---

## Task 2: Engine Integration

**Files:** `signals/engine.py`

- [ ] **Step 1: Tambah import datetime di engine.py**

Di bagian atas `signals/engine.py`, setelah existing imports, tambahkan:

```python
from datetime import datetime
from config import REGIME_WEIGHTS, KILL_ZONE_BONUS
```

- [ ] **Step 2: Tambah 2 fungsi baru di engine.py** (setelah `score_clamp`, sebelum `calc_trend_score`):

```python
def get_regime_weights(regime: str) -> dict:
    """
    Return bobot sinyal untuk regime yang diberikan.
    Fallback ke SIGNAL_WEIGHTS jika regime tidak dikenal.
    """
    return REGIME_WEIGHTS.get(regime, SIGNAL_WEIGHTS)


def get_kill_zone_modifier() -> tuple[bool, float]:
    """
    Return (in_kill_zone, modifier).
    Kill zones: London open 07-10 UTC, NY open 13-16 UTC.
    """
    now     = datetime.utcnow()
    now_min = now.hour * 60 + now.minute

    for (sh, sm, eh, em) in KILL_ZONES_UTC:
        start = sh * 60 + sm
        end   = eh * 60 + em
        if start <= now_min < end:
            return True, float(KILL_ZONE_BONUS)

    return False, 0.0
```

- [ ] **Step 3: Refactor score_coin() — pindah regime detection dan gunakan adaptive weights**

Di `score_coin()`, cari blok ini:

```python
    # Calculate all signals
    s = {}
    s["trend_score"]     = calc_trend_score(df_4h, df_1d)
    s["rsi_score"]       = calc_rsi_score(df_4h)
    s["macd_score"]      = calc_macd_score(df_4h)
    s["volume_score"]    = calc_volume_score(df_4h)
    s["wyckoff_score"]   = calc_wyckoff_score(df_4h)
    s["onchain_score"]   = calc_onchain_score(symbol, db)
    s["sentiment_score"] = calc_sentiment_score(fear_greed, funding_rate)

    # Weighted total — explicit mapping because SIGNAL_WEIGHTS keys differ from s dict keys
    total = (
        s["trend_score"]     * SIGNAL_WEIGHTS["trend_alignment"] +
        s["rsi_score"]       * SIGNAL_WEIGHTS["rsi_momentum"] +
        s["macd_score"]      * SIGNAL_WEIGHTS["macd_momentum"] +
        s["volume_score"]    * SIGNAL_WEIGHTS["volume_confirm"] +
        s["wyckoff_score"]   * SIGNAL_WEIGHTS["wyckoff_phase"] +
        s["onchain_score"]   * SIGNAL_WEIGHTS["onchain_signal"] +
        s["sentiment_score"] * SIGNAL_WEIGHTS["sentiment_score"]
    )
```

Ganti dengan:

```python
    # Calculate all signals
    s = {}
    s["trend_score"]     = calc_trend_score(df_4h, df_1d)
    s["rsi_score"]       = calc_rsi_score(df_4h)
    s["macd_score"]      = calc_macd_score(df_4h)
    s["volume_score"]    = calc_volume_score(df_4h)
    s["wyckoff_score"]   = calc_wyckoff_score(df_4h)
    s["onchain_score"]   = calc_onchain_score(symbol, db)
    s["sentiment_score"] = calc_sentiment_score(fear_greed, funding_rate)

    # Phase 6: detect regime first, then use adaptive weights
    regime  = detect_regime(df_4h)
    weights = get_regime_weights(regime)

    total = (
        s["trend_score"]     * weights["trend_alignment"] +
        s["rsi_score"]       * weights["rsi_momentum"] +
        s["macd_score"]      * weights["macd_momentum"] +
        s["volume_score"]    * weights["volume_confirm"] +
        s["wyckoff_score"]   * weights["wyckoff_phase"] +
        s["onchain_score"]   * weights["onchain_signal"] +
        s["sentiment_score"] * weights["sentiment_score"]
    )
```

- [ ] **Step 4: Tambah kill zone modifier di score_coin() — setelah Phase 5 block**

Cari:
```python
    # Phase 5: Social + Whale modifiers
    social_mod = get_social_modifier(symbol, db)
    whale_mod  = get_whale_modifier(symbol, db)
    total      = score_clamp(total + social_mod + whale_mod)

    regime = detect_regime(df_4h)
```

Ganti dengan:
```python
    # Phase 5: Social + Whale modifiers
    social_mod = get_social_modifier(symbol, db)
    whale_mod  = get_whale_modifier(symbol, db)
    total      = score_clamp(total + social_mod + whale_mod)

    # Phase 6: Kill zone bonus
    in_kill_zone, kz_mod = get_kill_zone_modifier()
    total = score_clamp(total + kz_mod)
```

Note: hapus baris `regime = detect_regime(df_4h)` yang lama di sini karena sudah dipindah ke atas.

- [ ] **Step 5: Update result dict — tambah keys baru:**

Cari result dict dan tambahkan 3 key baru:
```python
        "regime":           regime,           # sudah ada, pastikan masih pakai var yg dipindah
        ...
        "social_modifier":  social_mod,
        "whale_modifier":   whale_mod,
        "kill_zone_active": in_kill_zone,     # tambah
        "kill_zone_modifier": kz_mod,         # tambah
        "active_weights":   regime,           # tambah — menunjukkan weights set mana yang dipakai
```

- [ ] **Step 6: Update juga blocked return dict — tambah kill_zone_active:**

Di blok yang return saat news blocked, tambahkan:
```python
            "kill_zone_active":   False,
            "kill_zone_modifier": 0.0,
            "active_weights":     "BLOCKED",
```

- [ ] **Step 7: Jalankan tests/test_phase6.py — 6 passed:**

```
py -m pytest tests/test_phase6.py -v
```

- [ ] **Step 8: Full suite — semua pass:**

```
py -m pytest tests/ --tb=short 2>&1 | tail -5
```

Expected: 79 passed (73 lama + 6 baru)

- [ ] **Step 9: Verifikasi manual — cek regime weights dipakai:**

```
py -c "
import sys; sys.path.insert(0, '.')
from signals.engine import get_regime_weights, get_kill_zone_modifier
print('TRENDING_BULL trend weight:', get_regime_weights('TRENDING_BULL')['trend_alignment'])
print('RANGING rsi weight:', get_regime_weights('RANGING')['rsi_momentum'])
print('Default trend weight:', get_regime_weights('UNKNOWN')['trend_alignment'])
in_zone, mod = get_kill_zone_modifier()
print(f'Kill zone active: {in_zone}, modifier: {mod}')
"
```

- [ ] **Step 10: Commit:**

```
git add signals/engine.py
git commit -m "feat: Phase 6 complete — adaptive regime weights + kill zone modifier"
```
