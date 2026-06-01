"""Сериализация результатов: grid-таблица, summary, results.json, predictions.json.

Выделено из ``report.py`` без изменения логики. Публичные имена ре-экспортируются
из ``report`` для обратной совместимости импортов.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from backend.dataset.core import log
from backend.utils import to_json_safe as _to_json_safe

from .config import MODELS_DIR

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Grid search results
# ---------------------------------------------------------------------------

def save_grid_results(
    grid_df: pd.DataFrame,
    *,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Сохраняет таблицу grid search в CSV и выводит в консоль."""
    from backend.csv_io import save_csv
    path = save_csv(grid_df, output_dir / f"{prefix}_grid_results.csv")
    log(f"[report] Grid results → {path}")

    # Краткая таблица в stdout
    cols = [
        "combo", "iterations", "depth", "learning_rate",
        "l2_leaf_reg", "bagging_temperature", "border_count",
        "mean_rmse_cv", "std_rmse_cv",
        "sharpe", "dir_acc_pct", "mae_pct", "profit_factor",
        "accuracy", "elapsed_s",
    ]
    available = [c for c in cols if c in grid_df.columns]
    print("\nGrid Search Results (отсортировано по Sharpe ↓ / RMSE ↑):")
    print(grid_df[available].to_string(index=False, float_format=lambda v: f"{v:.6f}"))
    print()
    return path


# ---------------------------------------------------------------------------
# Итоговая сводка
# ---------------------------------------------------------------------------

def print_summary(
    metrics: dict[str, float],
    best_params: dict,
    model_path: Path,
) -> None:
    """Печатает итоговые метрики и путь к артефактам в stdout."""
    print("\n" + "=" * 60)
    print("РЕЗУЛЬТАТЫ НА ТЕСТОВОМ НАБОРЕ (walk-forward 30%)")
    print("=" * 60)
    for key, value in metrics.items():
        if isinstance(value, (int, float)):
            print(f"  {key:<25}: {value:.6f}")
        elif isinstance(value, dict):
            pass  # вложенные структуры (signal_details и т.п.) не выводим в однострочник
        else:
            print(f"  {key:<25}: {value!r}")
    print("\nЛУЧШИЕ ГИПЕРПАРАМЕТРЫ:")
    for key, value in best_params.items():
        print(f"  {key:<25}: {value}")
    print(f"\nМодель сохранена: {model_path}")
    print("=" * 60)


def save_results_json(
    metrics: dict[str, float],
    best_params: dict,
    model_path: Path,
    *,
    annualize_factor: float | None = None,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Сохраняет все метрики и гиперпараметры в JSON-файл."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_results.json"
    payload: dict = {
        "model_path": str(model_path),
        "best_params": best_params,
        "metrics": metrics,
    }
    if annualize_factor is not None:
        payload["annualize_factor"] = annualize_factor
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _LOG.info(
        "[report] Results JSON: R2=%.4f sharpe=%.4f dir_acc=%.1f%%",
        metrics.get("R2", float("nan")),
        metrics.get("sharpe", metrics.get("Sharpe", float("nan"))),
        metrics.get("dir_acc_pct", float("nan")),
    )
    log(f"[report] Results JSON → {path}")
    return path


# ---------------------------------------------------------------------------
# Сохранение полных предсказаний в JSON
# ---------------------------------------------------------------------------

def save_predictions_json(
    y_true: "np.ndarray | pd.Series",
    y_pred: "np.ndarray",
    timestamps: "pd.Series | None" = None,
    *,
    metrics: "dict | None" = None,
    best_params: "dict | None" = None,
    model_path: "Path | None" = None,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Сохраняет все предсказания на тестовой выборке в JSON.

    Структура файла {prefix}_predictions.json:
      prefix, saved_at, n_samples, [model_path], [metrics], [best_params],
      predictions: [{timestamp, y_true, y_pred, direction_correct}, ...]
    """
    import datetime as _dt

    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)

    # Сериализуем временны́е метки
    if timestamps is not None:
        ts_values: list = []
        for t in timestamps:
            try:
                ts_values.append(str(t))
            except Exception:
                ts_values.append(None)
    else:
        ts_values = list(range(len(y_true_arr)))

    predictions = [
        {
            "timestamp":         ts_values[i],
            "y_true":            float(y_true_arr[i]),
            "y_pred":            float(y_pred_arr[i]),
            "direction_correct": bool(np.sign(y_true_arr[i]) == np.sign(y_pred_arr[i])),
        }
        for i in range(len(y_true_arr))
    ]

    payload: dict = {
        "prefix":    prefix,
        "saved_at":  _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "n_samples": len(predictions),
    }
    if model_path is not None:
        payload["model_path"] = str(model_path)
    if metrics is not None:
        payload["metrics"] = _to_json_safe(metrics)
    if best_params is not None:
        payload["best_params"] = _to_json_safe(best_params)
    payload["predictions"] = predictions

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_predictions.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _dir_correct = sum(p["direction_correct"] for p in predictions)
    _dir_acc = 100.0 * _dir_correct / len(predictions) if predictions else 0.0
    _LOG.info(
        "[report] Predictions JSON: n=%d dir_acc=%.1f%% mean_y_true=%.6f mean_y_pred=%.6f",
        len(predictions), _dir_acc,
        float(np.mean(y_true_arr)) if len(y_true_arr) else float("nan"),
        float(np.mean(y_pred_arr)) if len(y_pred_arr) else float("nan"),
    )
    log(f"[report] Predictions JSON → {path} ({len(predictions):,} строк)")
    return path
