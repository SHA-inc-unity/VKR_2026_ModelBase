from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def plot_result(symbol: str, model_name: str, full_series: pd.Series, result_df: pd.DataFrame):
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=False)

    axes[0].plot(full_series.values, label=f"{symbol} close", color="steelblue")
    axes[0].set_title(f"{symbol}: очищенный ряд")
    axes[0].legend()

    n = len(result_df)
    axes[1].plot(np.arange(n), result_df["y_true"].values, label="y_true", color="black")
    axes[1].plot(np.arange(n), result_df["y_pred"].values, label="y_pred", color="tomato", alpha=0.9)
    axes[1].set_title(f"Факт vs прогноз ({model_name})")
    axes[1].legend()

    plt.tight_layout()
    plt.show()


def save_result_plot(symbol: str, model_name: str, full_series: pd.Series, result_df: pd.DataFrame, output_path: Path):
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=False)

    axes[0].plot(full_series.values, label=f"{symbol} close", color="steelblue")
    axes[0].set_title(f"{symbol}: очищенный ряд")
    axes[0].legend()

    n = len(result_df)
    axes[1].plot(np.arange(n), result_df["y_true"].values, label="y_true", color="black")
    axes[1].plot(np.arange(n), result_df["y_pred"].values, label="y_pred", color="tomato", alpha=0.9)
    axes[1].set_title(f"Факт vs прогноз ({model_name})")
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(output_path, dpi=140)
    plt.close(fig)


def export_all_results(output_dir: Path, symbol: str, full_series: pd.Series, all_results: Dict[str, dict]):
    if len(all_results) == 0:
        raise RuntimeError("Нет результатов для экспорта")

    run_ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"{symbol}_all_models_{run_ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for model_name in ["naive", "arima", "sarima", "ast"]:
        if model_name not in all_results:
            continue
        result = all_results[model_name]
        pred_df = result["pred_df"]
        model_metrics = result["metrics"]

        pred_path = run_dir / f"{symbol}_{model_name}_predictions.csv"
        plot_path = run_dir / f"{symbol}_{model_name}_plot.png"

        pred_df.to_csv(pred_path, index=False)
        save_result_plot(symbol, model_name, full_series, pred_df, plot_path)

        row = {"symbol": symbol, "model": model_name}
        row.update({k: float(v) for k, v in model_metrics.items()})
        rows.append(row)

    metrics_df = pd.DataFrame(rows)
    if len(metrics_df) == 0:
        raise RuntimeError("Нет сохраненных результатов для naive/arima/sarima/ast")

    metric_cols = [c for c in ["MAE", "RMSE", "MAPE"] if c in metrics_df.columns]
    if metric_cols:
        metrics_df = metrics_df.sort_values(metric_cols[0]).reset_index(drop=True)

    metrics_path = run_dir / f"{symbol}_all_models_metrics.csv"
    metrics_df.to_csv(metrics_path, index=False)

    return run_dir, metrics_path, metrics_df
