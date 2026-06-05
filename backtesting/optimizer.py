# backtesting/optimizer.py
"""
Optuna Bayesian optimizer untuk menemukan bobot sinyal terbaik.
Objective: maksimalkan Sharpe ratio pada training set.
Deployment: hanya jika validation Sharpe > 0.8.
"""
import json
from datetime import datetime
from loguru import logger
import numpy as np
import pandas as pd

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
except ImportError:
    raise ImportError("Install optuna: pip install optuna")

from backtesting.harness import (
    build_signal_series,
    simulate_trades,
    replay_scores_for_coin,
    run_backtest,
)
from backtesting.walk_forward import get_splits, should_deploy, update_config_weights
from database import get_db
from config import COINS, SIGNAL_THRESHOLD, STOP_LOSS_PCT


def normalize_weights(raw: dict) -> dict:
    """Normalize bobot agar total = 1.0."""
    total = sum(raw.values())
    return {k: round(v / total, 4) for k, v in raw.items()}


def objective(trial, splits: dict, db) -> float:
    """
    Optuna objective function.
    Propose 7 weights, run backtest on training set, return Sharpe proxy.
    """
    raw = {
        "trend_alignment":  trial.suggest_float("trend",    0.05, 0.30),
        "rsi_momentum":     trial.suggest_float("rsi",      0.05, 0.25),
        "macd_momentum":    trial.suggest_float("macd",     0.05, 0.20),
        "volume_confirm":   trial.suggest_float("vol",      0.05, 0.25),
        "wyckoff_phase":    trial.suggest_float("wyck",     0.05, 0.25),
        "onchain_signal":   trial.suggest_float("chain",    0.05, 0.25),
        "sentiment_score":  trial.suggest_float("sent",     0.05, 0.20),
    }
    weights = normalize_weights(raw)

    all_pnl = []
    all_r   = []

    for symbol, info in COINS.items():
        try:
            tier     = info["tier"]
            stop_pct = STOP_LOSS_PCT.get(tier, 0.10)
            tp1_pct  = stop_pct * 2.5

            scores   = replay_scores_for_coin(
                symbol, weights,
                splits["train_start"], splits["train_end"], db
            )
            if len(scores) < 30:
                continue

            price_df = db.get_candles(symbol, "4h", limit=5000)
            price_df["timestamp"] = pd.to_datetime(price_df["timestamp"])
            price_df = price_df[
                (price_df["timestamp"] >= pd.Timestamp(splits["train_start"])) &
                (price_df["timestamp"] <= pd.Timestamp(splits["train_end"]))
            ].reset_index(drop=True)

            if price_df.empty or len(price_df) < 50:
                continue
            merged = price_df.set_index("timestamp").join(
                scores.rename("score"), how="left"
            ).fillna(50).reset_index()

            score_series = pd.Series(merged["score"].values, index=range(len(merged)))
            signals = build_signal_series(score_series, threshold=SIGNAL_THRESHOLD)
            result  = simulate_trades(merged, signals, stop_pct=stop_pct, tp1_pct=tp1_pct)

            if not result["trades"].empty:
                all_pnl.extend(result["trades"]["pnl_pct"].tolist())
                all_r.extend(result["trades"]["r_multiple"].tolist())

        except Exception:
            continue

    if len(all_pnl) < 10:
        return -999.0

    win_rate = sum(1 for p in all_pnl if p > 0) / len(all_pnl)
    pnl_arr  = np.array(all_pnl)
    sharpe   = (pnl_arr.mean() / pnl_arr.std()) * (2190 ** 0.5) if pnl_arr.std() > 0 else 0
    trade_bonus = 0.1 if len(all_pnl) >= 30 else -0.5

    return sharpe + trade_bonus


def run_optimization(n_trials: int = 300):
    """
    Jalankan Optuna optimization.
    Dipanggil dari main.py --optimize-weights.
    """
    from utils.telegram import send

    db     = get_db()
    splits = get_splits()

    logger.info(f"Starting Optuna optimization ({n_trials} trials)...")
    logger.info(f"Training: {splits['train_start']} → {splits['train_end']}")
    logger.info(f"Validation: {splits['val_start']} → {splits['val_end']}")

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )

    study.optimize(
        lambda trial: objective(trial, splits, db),
        n_trials=n_trials,
        show_progress_bar=True,
    )

    best_params = study.best_params
    best_value  = study.best_value

    raw_weights = {
        "trend_alignment":  best_params["trend"],
        "rsi_momentum":     best_params["rsi"],
        "macd_momentum":    best_params["macd"],
        "volume_confirm":   best_params["vol"],
        "wyckoff_phase":    best_params["wyck"],
        "onchain_signal":   best_params["chain"],
        "sentiment_score":  best_params["sent"],
    }
    best_weights = normalize_weights(raw_weights)

    logger.info(f"\nBest training Sharpe: {best_value:.3f}")
    logger.info("Best weights found:")
    for k, v in best_weights.items():
        logger.info(f"  {k}: {v:.3f}")

    logger.info(f"\nValidating on holdout set ({splits['val_start']} → {splits['val_end']})...")

    train_result = run_backtest(splits["train_start"], splits["train_end"], weights=best_weights)
    val_result   = run_backtest(splits["val_start"],   splits["val_end"],   weights=best_weights)

    train_metrics = {
        "sharpe":   train_result.get("sharpe", 0),   # real Sharpe from harness
        "win_rate": train_result.get("win_rate", 0),
    }
    val_metrics = {
        "sharpe":   val_result.get("sharpe", 0),     # real Sharpe from harness
        "win_rate": val_result.get("win_rate", 0),
    }

    deploy, reason = should_deploy(train_metrics, val_metrics)

    run_id = f"OPT-{datetime.utcnow().strftime('%Y%m%d-%H%M')}"
    db.save_backtest_result({
        "run_id":         run_id,
        "weights_json":   json.dumps(best_weights),
        "train_start":    splits["train_start"],
        "train_end":      splits["train_end"],
        "val_start":      splits["val_start"],
        "val_end":        splits["val_end"],
        "train_win_rate": train_result.get("win_rate", 0) * 100,
        "val_win_rate":   val_result.get("win_rate", 0) * 100,
        "train_sharpe":   train_metrics["sharpe"],
        "val_sharpe":     val_metrics["sharpe"],
        "total_trades":   train_result.get("total_trades", 0),
        "avg_r":          train_result.get("avg_r", 0),
        "max_drawdown":   round(-abs(val_result.get("avg_r", 0.1)) * 0.1, 4),
        "deployed":       deploy,
    })

    if deploy:
        logger.info(f"\n✅ DEPLOYING new weights: {reason}")
        update_config_weights(best_weights)
        send(f"✅ <b>APEX Weight Optimizer</b>\n"
             f"New weights deployed ({run_id})\n"
             f"Val Sharpe: {val_metrics['sharpe']:.2f} | "
             f"Win rate: {val_metrics['win_rate']*100:.1f}%\n"
             f"Reason: {reason}")
    else:
        logger.warning(f"\n⚠️  NOT deploying: {reason}")
        send(f"⚠️ <b>APEX Optimizer — Not Deployed</b>\n"
             f"Run: {run_id}\nReason: {reason}")

    print(f"\n{'═'*50}")
    print(f"Optimization complete: {run_id}")
    print(f"Deploy: {'YES ✅' if deploy else 'NO ⚠️'}")
    print(f"Reason: {reason}")
    print(f"{'═'*50}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8: Per-regime Optuna optimizer
# ─────────────────────────────────────────────────────────────────────────────

def _compute_fitness(weights: dict, historical_signals: list, labels: list) -> float:
    """
    Compute composite fitness for a weight dict given historical signal dicts and returns.

    Args:
        weights: {signal_id: float} — must sum to 1.0
        historical_signals: list of {signal_id: float 0-100} — one per period
        labels: list of floats — % return for next period (positive = good)

    Returns:
        Fitness score in [0, 1].
        Fitness = 0.40*sharpe_norm + 0.25*win_rate + 0.20*(1-max_dd) + 0.15*pf_norm
    """
    n = min(len(historical_signals), len(labels))
    if n == 0:
        return 0.0

    # Step 1-2: compute composite and buy signals
    composites = []
    for i in range(n):
        sig_dict = historical_signals[i]
        composite = sum(weights.get(sid, 0.0) * sig_dict.get(sid, 50.0) for sid in weights)
        composites.append(composite)

    signal_flags = [1 if c > 60 else 0 for c in composites]

    # Step 3: realized returns (only when signaled)
    realized = [labels[i] * signal_flags[i] for i in range(n)]

    # Step 4: Sharpe (annualized with sqrt(365*6) ≈ sqrt(2190))
    active = [r for r in realized if r != 0]
    if len(active) < 2:
        sharpe = 0.0
    else:
        arr = np.array(active, dtype=float)
        std = arr.std()
        sharpe = (arr.mean() / std) * (2190 ** 0.5) if std > 0 else 0.0

    # Step 5: Win rate
    non_zero = [r for r in realized if r != 0]
    if non_zero:
        win_rate = sum(1 for r in non_zero if r > 0) / len(non_zero)
    else:
        win_rate = 0.0

    # Step 6: Max drawdown from cumulative equity curve
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in realized:
        equity += r
        if equity > peak:
            peak = equity
        dd = (peak - equity) / max(abs(peak), 1e-9)
        if dd > max_dd:
            max_dd = dd
    max_dd = min(max_dd, 1.0)

    # Step 7: Profit factor
    gains = sum(r for r in realized if r > 0)
    losses = abs(sum(r for r in realized if r < 0))
    profit_factor = gains / max(losses, 0.001)

    # Normalize components to [0, 1]
    sharpe_norm = min(max(sharpe / 3.0, 0.0), 1.0)
    pf_norm = min(profit_factor / 3.0, 1.0)

    fitness = (
        0.40 * sharpe_norm
        + 0.25 * win_rate
        + 0.20 * (1.0 - max_dd)
        + 0.15 * pf_norm
    )
    return float(min(max(fitness, 0.0), 1.0))


def optimize_weights_for_regime(
    regime: str,
    historical_signals: list,
    labels: list,
    n_trials: int = 300,
) -> dict:
    """
    Run Optuna optimization to find best signal weights for a given market regime.

    Args:
        regime: One of "bull", "bear", "sideways", "volatile", "recovery"
        historical_signals: list of {signal_id: float} — one dict per candle period
        labels: list of floats — % return for the next period
        n_trials: Number of Optuna trials (default 300)

    Returns:
        Normalized weight dict {signal_id: float} summing to 1.0
    """
    from signals.registry import get_signal_ids
    from config import DEFAULT_WEIGHTS_PHASE8

    signal_ids = get_signal_ids()
    default_weights = DEFAULT_WEIGHTS_PHASE8.get(regime, DEFAULT_WEIGHTS_PHASE8.get("bull", {}))

    def _objective(trial):
        raw = {sid: trial.suggest_float(sid, 0.001, 0.3) for sid in signal_ids}
        total = sum(raw.values())
        weights = {sid: v / total for sid, v in raw.items()}
        return _compute_fitness(weights, historical_signals, labels)

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )

    # Seed with default weights as the first trial
    seed_params = {sid: float(default_weights.get(sid, 0.01)) for sid in signal_ids}
    study.enqueue_trial(seed_params)

    study.optimize(_objective, n_trials=n_trials, show_progress_bar=False)

    best = study.best_params
    total = sum(best.values())
    return {sid: v / total for sid, v in best.items()}


def optimize_all_regimes(
    historical_data_by_regime: dict,
    n_trials: int = 300,
) -> dict:
    """
    Optimize signal weights for all 5 market regimes.

    Args:
        historical_data_by_regime: {regime: (historical_signals, labels)}
            where historical_signals is list of {signal_id: float}
            and labels is list of floats (% returns)
        n_trials: Number of Optuna trials per regime (default 300)

    Returns:
        {regime: {signal_id: float}} — normalized weights for each regime
    """
    results = {}
    for regime, (signals, labels) in historical_data_by_regime.items():
        logger.info(f"Optimizing weights for regime: {regime} ({n_trials} trials)...")
        weights = optimize_weights_for_regime(regime, signals, labels, n_trials=n_trials)
        results[regime] = weights
        logger.info(f"  Done — regime={regime}, top signal: {max(weights, key=weights.get)}")
    return results
