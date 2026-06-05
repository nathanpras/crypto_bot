# backtesting/walk_forward.py
"""
Walk-forward validation untuk mencegah overfitting.
Train: 18 bulan pertama. Validation: 6 bulan terakhir.
Hanya deploy bobot baru jika val_sharpe > 0.8 DAN val_win_rate > 55%.
"""
from datetime import date
from loguru import logger


# Split disesuaikan dengan data yang tersedia (mulai Jun 2024)
# Train: 12 bulan pertama, Val: 6 bulan terakhir
TRAIN_START = "2024-06-01"
TRAIN_END   = "2025-05-31"
VAL_START   = "2025-06-01"
VAL_END     = "2025-12-31"

# Deployment criteria
MIN_VAL_SHARPE   = 0.8
MIN_VAL_WIN_RATE = 0.55


def get_splits() -> dict:
    """Return tanggal train/val split."""
    return {
        "train_start": TRAIN_START,
        "train_end":   TRAIN_END,
        "val_start":   VAL_START,
        "val_end":     VAL_END,
    }


def should_deploy(train_metrics: dict, val_metrics: dict) -> tuple:
    """
    Cek apakah bobot hasil optimasi layak dideploy ke production.
    Return: (should_deploy: bool, reason: str)
    """
    val_sharpe   = val_metrics.get("sharpe", 0)
    val_win_rate = val_metrics.get("win_rate", 0)
    train_sharpe = train_metrics.get("sharpe", 0)

    if val_sharpe < MIN_VAL_SHARPE:
        return False, (
            f"Val Sharpe {val_sharpe:.2f} < minimum {MIN_VAL_SHARPE} "
            f"(possible overfitting)"
        )

    if val_win_rate < MIN_VAL_WIN_RATE:
        return False, (
            f"Val win rate {val_win_rate*100:.1f}% < minimum "
            f"{MIN_VAL_WIN_RATE*100:.0f}%"
        )

    # Sanity check: val tidak jauh lebih buruk dari train
    if train_sharpe > 0 and val_sharpe < train_sharpe * 0.4:
        return False, (
            f"Val Sharpe {val_sharpe:.2f} is < 40% of train Sharpe "
            f"{train_sharpe:.2f} — overfitting detected"
        )

    return True, (
        f"Val Sharpe {val_sharpe:.2f} >= {MIN_VAL_SHARPE}, "
        f"Win rate {val_win_rate*100:.1f}% >= {MIN_VAL_WIN_RATE*100:.0f}%"
    )


def update_config_weights(weights: dict) -> bool:
    """
    Update SIGNAL_WEIGHTS di config.py dengan bobot optimal baru.
    Buat backup config_backup.py sebelum modifikasi.
    """
    import shutil
    import re

    config_path = "config.py"
    backup_path = "config_backup.py"

    shutil.copy(config_path, backup_path)
    logger.info(f"Config backed up to {backup_path}")

    with open(config_path, "r") as f:
        content = f.read()

    weights_str = "SIGNAL_WEIGHTS = {\n"
    for key, val in weights.items():
        weights_str += f'    "{key}":{" " * (20 - len(key))}{val:.3f},\n'
    weights_str += "}"

    pattern = r'SIGNAL_WEIGHTS\s*=\s*\{[^}]+\}'
    new_content = re.sub(pattern, weights_str, content, flags=re.DOTALL)

    if new_content == content:
        logger.error("Failed to update SIGNAL_WEIGHTS — pattern not found in config.py")
        return False

    with open(config_path, "w") as f:
        f.write(new_content)

    logger.info("SIGNAL_WEIGHTS updated in config.py")
    for k, v in weights.items():
        logger.info(f"  {k}: {v:.3f}")

    return True


# ---------------------------------------------------------------------------
# Phase 8 — Rolling Walk-Forward Helpers
# ---------------------------------------------------------------------------

def rolling_walk_forward_splits(
    data: list,
    train_months: int = 9,
    val_months: int = 3,
) -> list:
    """
    Generate rolling walk-forward splits from a flat list of data points.

    Treats every element as one daily bar (30 bars ≈ 1 month).  Each split
    contains a 'train' window of *train_months* × 30 bars immediately
    followed by a 'val' window of *val_months* × 30 bars.  The window then
    steps forward by *val_months* × 30 bars.

    Returns a list of dicts::

        [{"train": [...], "val": [...], "window_idx": int}, ...]

    Returns an empty list when there is not enough data for at least 6
    complete validation windows.

    Parameters
    ----------
    data:
        List of dicts.  No particular keys are required.
    train_months:
        Number of months in the training window (default 9).
    val_months:
        Number of months in the validation window / step size (default 3).
    """
    bars_per_month = 30
    train_bars = train_months * bars_per_month
    val_bars   = val_months  * bars_per_month
    step_bars  = bars_per_month  # step one month at a time for maximum coverage

    splits = []
    window_idx = 0
    start = 0

    while True:
        train_end = start + train_bars
        val_end   = train_end + val_bars

        if val_end > len(data):
            break

        splits.append(
            {
                "train":      data[start:train_end],
                "val":        data[train_end:val_end],
                "window_idx": window_idx,
            }
        )
        window_idx += 1
        start      += step_bars

    # Require at least 6 complete validation windows
    if len(splits) < 6:
        return []

    return splits


def is_consistent(val_scores: list, std_threshold: float = 0.15) -> bool:
    """
    Return True when walk-forward results are considered consistent.

    Consistency requires:
    * At least 6 validation windows (``len(val_scores) >= 6``).
    * The standard deviation of the scores is below *std_threshold*.

    Parameters
    ----------
    val_scores:
        List of float fitness scores from each walk-forward validation window.
    std_threshold:
        Maximum allowed standard deviation (default 0.15).
    """
    if len(val_scores) < 6:
        return False

    n    = len(val_scores)
    mean = sum(val_scores) / n
    variance = sum((x - mean) ** 2 for x in val_scores) / n
    std  = variance ** 0.5

    return std < std_threshold
