# backtesting/harness.py
"""
Backtesting harness untuk APEX trading strategy.
Replay historical signals dari DuckDB dan simulate trades dengan
realistic assumptions: slippage, fee, TP1/TP2 partial exits.
"""
import json
import uuid
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from config import COINS, SIGNAL_THRESHOLD, SIGNAL_STRONG, STOP_LOSS_PCT
from database import get_db


def build_signal_series(scores: pd.Series, threshold: float = 70) -> pd.Series:
    """Konversi score series ke boolean signal series."""
    return scores >= threshold


def simulate_trades(
    price_df: pd.DataFrame,
    signals: pd.Series,
    stop_pct:    float = 0.10,
    tp1_pct:     float = 0.25,
    fee_pct:     float = 0.001,
    slippage:    float = 0.0005,
    max_hold_candles: int = 252,
) -> dict:
    """
    Simulasi semua trade berdasarkan signal series.
    Entry: open candle berikutnya setelah signal (realistic).
    Exit: TP1 atau stop loss, whichever comes first.
    """
    trades = []
    in_trade = False
    entry_price = 0.0
    stop_price  = 0.0
    tp1_price   = 0.0
    entry_idx   = 0
    initial_capital = 10_000.0
    capital     = initial_capital
    position_pct = 0.30

    close  = price_df["close"].values
    high   = price_df["high"].values
    low    = price_df["low"].values
    n      = len(price_df)

    for i in range(1, n):
        if not in_trade and signals.iloc[i - 1]:
            entry_price = close[i - 1] * (1 + slippage)
            stop_price  = entry_price * (1 - stop_pct)
            tp1_price   = entry_price * (1 + tp1_pct)
            entry_idx   = i
            in_trade    = True
            continue

        if in_trade:
            candles_held = i - entry_idx

            if low[i] <= stop_price:
                exit_price = stop_price * (1 - slippage)
                pnl_pct    = (exit_price / entry_price - 1) - fee_pct * 2
                risk       = (entry_price - stop_price) / entry_price
                r_multiple = pnl_pct / risk if risk > 0 else 0
                trades.append({
                    "entry_idx":    entry_idx,
                    "exit_idx":     i,
                    "entry_price":  entry_price,
                    "exit_price":   exit_price,
                    "exit_reason":  "stop",
                    "pnl_pct":      pnl_pct,
                    "r_multiple":   r_multiple,
                    "hold_candles": candles_held,
                })
                capital  *= (1 + pnl_pct * position_pct)
                in_trade  = False
                continue

            if high[i] >= tp1_price:
                exit_price = tp1_price * (1 - slippage)
                pnl_pct    = (exit_price / entry_price - 1) - fee_pct * 2
                risk       = (entry_price - stop_price) / entry_price
                r_multiple = pnl_pct / risk if risk > 0 else 0
                trades.append({
                    "entry_idx":    entry_idx,
                    "exit_idx":     i,
                    "entry_price":  entry_price,
                    "exit_price":   exit_price,
                    "exit_reason":  "tp1",
                    "pnl_pct":      pnl_pct,
                    "r_multiple":   r_multiple,
                    "hold_candles": candles_held,
                })
                capital  *= (1 + pnl_pct * position_pct)
                in_trade  = False
                continue

            if candles_held >= max_hold_candles:
                exit_price = close[i] * (1 - slippage)
                pnl_pct    = (exit_price / entry_price - 1) - fee_pct * 2
                risk       = (entry_price - stop_price) / entry_price
                r_multiple = pnl_pct / risk if risk > 0 else 0
                trades.append({
                    "entry_idx":    entry_idx,
                    "exit_idx":     i,
                    "entry_price":  entry_price,
                    "exit_price":   exit_price,
                    "exit_reason":  "timeout",
                    "pnl_pct":      pnl_pct,
                    "r_multiple":   r_multiple,
                    "hold_candles": candles_held,
                })
                capital  *= (1 + pnl_pct * position_pct)
                in_trade  = False

    if in_trade and len(close) > 0:
        exit_price = close[-1]
        pnl_pct    = (exit_price / entry_price - 1) - fee_pct * 2
        risk       = (entry_price - stop_price) / entry_price
        r_multiple = pnl_pct / risk if risk > 0 else 0
        trades.append({
            "entry_idx":    entry_idx,
            "exit_idx":     n - 1,
            "entry_price":  entry_price,
            "exit_price":   exit_price,
            "exit_reason":  "end_of_data",
            "pnl_pct":      pnl_pct,
            "r_multiple":   r_multiple,
            "hold_candles": n - 1 - entry_idx,
        })

    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["entry_idx", "exit_idx", "entry_price", "exit_price",
                 "exit_reason", "pnl_pct", "r_multiple", "hold_candles"]
    )

    equity = [initial_capital]
    cap = initial_capital
    trade_by_exit = {t["exit_idx"]: t for t in trades} if trades else {}
    for i in range(1, n):
        if i in trade_by_exit:
            cap *= (1 + trade_by_exit[i]["pnl_pct"] * position_pct)
        equity.append(cap)

    return {
        "trades":       trades_df,
        "equity_curve": pd.Series(equity, index=price_df.index[:len(equity)]),
        "total_trades": len(trades_df),
    }


def calc_metrics(result: dict) -> dict:
    """Hitung performance metrics dari hasil simulasi."""
    trades = result["trades"]
    equity = result["equity_curve"]

    if trades.empty or result["total_trades"] == 0:
        return {
            "win_rate":     0.0,
            "sharpe":       0.0,
            "avg_r":        0.0,
            "max_drawdown": 0.0,
            "total_trades": 0,
            "win_trades":   0,
        }

    win_trades  = (trades["pnl_pct"] > 0).sum()
    win_rate    = win_trades / len(trades)
    avg_r       = trades["r_multiple"].mean()

    returns = equity.pct_change().dropna()
    if returns.std() > 0:
        sharpe = (returns.mean() / returns.std()) * np.sqrt(2190)
    else:
        sharpe = 0.0

    peak = equity.expanding().max()
    dd   = (equity - peak) / peak
    max_drawdown = dd.min()

    return {
        "win_rate":     round(float(win_rate), 4),
        "sharpe":       round(float(sharpe), 4),
        "avg_r":        round(float(avg_r), 4),
        "max_drawdown": round(float(max_drawdown), 4),
        "total_trades": int(result["total_trades"]),
        "win_trades":   int(win_trades),
    }


def replay_scores_for_coin(
    symbol:    str,
    weights:   dict,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    db=None,
) -> pd.Series:
    """
    Replay signal scores untuk satu coin menggunakan bobot yang diberikan.
    Baca raw sub-scores dari tabel signals di DB, apply weights.
    """
    if db is None:
        db = get_db()

    query = """
        SELECT timestamp, trend_score, rsi_score, macd_score,
               volume_score, wyckoff_score, onchain_score, sentiment_score
        FROM signals
        WHERE symbol = ?
    """
    params = [symbol]

    if date_from:
        query += " AND timestamp >= ?"
        params.append(date_from)
    if date_to:
        query += " AND timestamp <= ?"
        params.append(date_to)

    query += " ORDER BY timestamp"

    df = db.conn.execute(query, params).df()
    if df.empty:
        return pd.Series(dtype=float)

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")

    score_map = {
        "trend_score":     weights.get("trend_alignment",  0.20),
        "rsi_score":       weights.get("rsi_momentum",     0.15),
        "macd_score":      weights.get("macd_momentum",    0.10),
        "volume_score":    weights.get("volume_confirm",   0.15),
        "wyckoff_score":   weights.get("wyckoff_phase",    0.15),
        "onchain_score":   weights.get("onchain_signal",   0.15),
        "sentiment_score": weights.get("sentiment_score",  0.10),
    }

    total = pd.Series(0.0, index=df.index)
    for col, w in score_map.items():
        if col in df.columns:
            total += df[col].fillna(50) * w

    return total


def run_backtest(
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    weights:   Optional[dict] = None,
) -> dict:
    """
    Jalankan backtest untuk semua coin, aggregate results.
    Dipanggil dari main.py --backtest.
    """
    from config import SIGNAL_WEIGHTS

    if weights is None:
        weights = SIGNAL_WEIGHTS

    if date_from is None:
        date_from = "2023-01-01"
    if date_to is None:
        date_to = "2024-12-31"

    db = get_db()
    logger.info(f"Running backtest: {date_from} → {date_to}")

    all_trades = []

    for symbol, info in COINS.items():
        try:
            tier     = info["tier"]
            stop_pct = STOP_LOSS_PCT.get(tier, 0.10)
            tp1_pct  = stop_pct * 2.5

            scores = replay_scores_for_coin(symbol, weights, date_from, date_to, db)
            if len(scores) < 50:
                logger.debug(f"  {symbol}: insufficient signal history, skipping")
                continue

            price_df = db.get_candles(symbol, "4h")
            price_df = price_df[
                (price_df["timestamp"] >= date_from) &
                (price_df["timestamp"] <= date_to)
            ].reset_index(drop=True)

            if price_df.empty or len(price_df) < 50:
                continue

            price_df["timestamp"] = pd.to_datetime(price_df["timestamp"])
            merged = price_df.set_index("timestamp").join(
                scores.rename("score"), how="left"
            ).fillna(50)
            merged = merged.reset_index()

            score_series = pd.Series(
                merged["score"].values, index=range(len(merged))
            )

            signals = build_signal_series(score_series, threshold=SIGNAL_THRESHOLD)
            result  = simulate_trades(merged, signals, stop_pct=stop_pct, tp1_pct=tp1_pct)

            if not result["trades"].empty:
                result["trades"]["symbol"] = symbol
                all_trades.append(result["trades"])
                logger.debug(f"  {symbol}: {result['total_trades']} trades")

        except Exception as e:
            logger.warning(f"  Backtest failed for {symbol}: {e}")

    if not all_trades:
        logger.warning("No trades simulated — ensure --fetch-history was run first")
        return {}

    combined_trades = pd.concat(all_trades, ignore_index=True)
    win_rate     = (combined_trades["pnl_pct"] > 0).mean()
    avg_r        = combined_trades["r_multiple"].mean()
    total_trades = len(combined_trades)

    by_symbol = combined_trades.groupby("symbol").agg(
        trades=("pnl_pct", "count"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean()),
        avg_r=("r_multiple", "mean"),
    ).round(3)

    print("\n" + "═"*55)
    print(f"{'APEX Backtest Report':^55}")
    print(f"  Period: {date_from} → {date_to}")
    print("═"*55)
    print(f"  Total trades  : {total_trades}")
    print(f"  Win rate      : {win_rate*100:.1f}%")
    print(f"  Avg R-multiple: {avg_r:.2f}R")
    print(f"\n  Per-coin breakdown:")
    for sym, row in by_symbol.iterrows():
        print(f"    {sym:<12} {int(row['trades']):>3} trades | "
              f"{row['win_rate']*100:.0f}% WR | {row['avg_r']:.2f}R avg")
    print("═"*55 + "\n")

    # Compute real Sharpe from combined equity curve
    all_pnl_series = combined_trades["pnl_pct"].values
    if len(all_pnl_series) > 1 and all_pnl_series.std() > 0:
        sharpe = float((all_pnl_series.mean() / all_pnl_series.std()) * np.sqrt(2190))
    else:
        sharpe = 0.0

    return {
        "total_trades": total_trades,
        "win_rate":     round(float(win_rate), 4),
        "avg_r":        round(float(avg_r), 4),
        "sharpe":       round(sharpe, 4),
        "date_from":    date_from,
        "date_to":      date_to,
        "weights":      weights,
    }
