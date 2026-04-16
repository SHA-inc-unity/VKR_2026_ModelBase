"""Метрики качества модели: MAE, RMSE, R², Sharpe ratio."""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_metrics(
    y_true: "np.ndarray | pd.Series",
    y_pred: "np.ndarray | pd.Series",
    *,
    annualize_factor: float = 1.0,
) -> dict[str, float]:
    """Вычисляет MAE, RMSE, R² и Sharpe ratio стратегии «знак прогноза».

    Sharpe ratio считается для стратегии long/short:
        strategy_return[i] = sign(y_pred[i]) * y_true[i]

    Аргументы:
        annualize_factor — количество баров в году для аннуализации Sharpe.
                           Передавать SECONDS_PER_YEAR * 1000 / step_ms.
    """
    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)

    mae = float(np.mean(np.abs(y_true_arr - y_pred_arr)))
    rmse = float(np.sqrt(np.mean((y_true_arr - y_pred_arr) ** 2)))

    ss_res = np.sum((y_true_arr - y_pred_arr) ** 2)
    ss_tot = np.sum((y_true_arr - np.mean(y_true_arr)) ** 2)
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 1e-15 else 0.0

    # Sharpe ratio стратегии
    strategy_returns = np.sign(y_pred_arr) * y_true_arr
    mean_ret = float(np.mean(strategy_returns))
    std_ret = float(np.std(strategy_returns, ddof=1))
    sharpe = (mean_ret / std_ret * float(np.sqrt(annualize_factor))) if std_ret > 1e-12 else 0.0

    return {"MAE": mae, "RMSE": rmse, "R2": r2, "Sharpe": sharpe}


def compute_direction_metrics(
    y_true: "np.ndarray | pd.Series",
    y_pred: "np.ndarray | pd.Series",
) -> dict:
    """Вычисляет TP, TN, FP, FN и accuracy по знаку прогноза.

    Положительный класс — рост цены (target_return_1 > 0).
    Матрица собирается на агрегированных предсказаниях (не per-fold).
    """
    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)

    actual_up = y_true_arr > 0
    pred_up   = y_pred_arr > 0

    tp = int(np.sum( pred_up &  actual_up))
    tn = int(np.sum(~pred_up & ~actual_up))
    fp = int(np.sum( pred_up & ~actual_up))
    fn = int(np.sum(~pred_up &  actual_up))
    total = tp + tn + fp + fn
    accuracy = (tp + tn) / total if total > 0 else 0.0

    return {"TP": tp, "TN": tn, "FP": fp, "FN": fn, "accuracy": accuracy}


def compute_trading_metrics(
    y_true: "np.ndarray | pd.Series",
    y_pred: "np.ndarray | pd.Series",
    *,
    annualize_factor: float = 1.0,
) -> dict[str, float]:
    """Торговые метрики для CV-строки и финального теста.

    Возвращает:
        sharpe        — Sharpe Ratio стратегии «знак прогноза» (аннуализированный)
        dir_acc_pct   — Directional Accuracy, % (доля правильных знаков)
        mae_pct       — MAE в процентах от среднего |y_true|
        profit_factor — сумма прибыли / сумма убытка по стратегии
    """
    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)

    # Стратегия: лонг если pred > 0, шорт если pred < 0
    strategy_returns = np.sign(y_pred_arr) * y_true_arr

    # Sharpe
    mean_ret = float(np.mean(strategy_returns))
    std_ret  = float(np.std(strategy_returns, ddof=1))
    sharpe   = (mean_ret / std_ret * float(np.sqrt(annualize_factor))) if std_ret > 1e-12 else 0.0

    # Directional Accuracy %
    correct = np.sign(y_pred_arr) == np.sign(y_true_arr)
    dir_acc_pct = float(np.mean(correct) * 100.0)

    # MAE %
    denom = float(np.mean(np.abs(y_true_arr)))
    mae   = float(np.mean(np.abs(y_true_arr - y_pred_arr)))
    mae_pct = (mae / denom * 100.0) if denom > 1e-15 else 0.0

    # Profit Factor
    profits = strategy_returns[strategy_returns > 0]
    losses  = strategy_returns[strategy_returns < 0]
    gross_profit = float(np.sum(profits)) if len(profits) > 0 else 0.0
    gross_loss   = float(np.abs(np.sum(losses))) if len(losses) > 0 else 1e-12
    profit_factor = gross_profit / gross_loss if gross_loss > 1e-15 else float("inf")

    return {
        "sharpe":        sharpe,
        "dir_acc_pct":   dir_acc_pct,
        "mae_pct":       mae_pct,
        "profit_factor": profit_factor,
    }
