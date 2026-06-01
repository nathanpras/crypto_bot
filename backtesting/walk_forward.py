# backtesting/walk_forward.py
"""
Walk-forward validation untuk mencegah overfitting.
Train: 18 bulan pertama. Validation: 6 bulan terakhir.
Hanya deploy bobot baru jika val_sharpe > 0.8 DAN val_win_rate > 55%.
"""
from datetime import date
from loguru import logger


# Default split: train = Jan 2023 – Jun 2024, val = Jul 2024 – Dec 2024
TRAIN_START = "2023-01-01"
TRAIN_END   = "2024-06-30"
VAL_START   = "2024-07-01"
VAL_END     = "2024-12-31"

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
