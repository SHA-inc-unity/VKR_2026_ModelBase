"""Генерация отчётов: feature importance TOP-20, actual vs predicted, P&L curve, grid results."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

_LOG = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MATPLOTLIB = True
except ImportError:
    _HAS_MATPLOTLIB = False

from backend.dataset.core import log

from .config import MODELS_DIR


# ---------------------------------------------------------------------------
# Feature importance TOP-N
# ---------------------------------------------------------------------------

def plot_feature_importance(
    model: object,
    feature_names: list[str],
    *,
    top_n: int = 20,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Строит горизонтальный bar-chart топ-N важных признаков и сохраняет PNG.

    Использует встроенный метод get_feature_importance() CatBoost
    (предсказание на основе разбиений в деревьях, не SHAP).
    """
    if not _HAS_MATPLOTLIB:
        raise ImportError("Установи matplotlib: pip install matplotlib")

    importances = model.get_feature_importance()  # type: ignore[attr-defined]
    fi = pd.Series(importances, index=feature_names).sort_values(ascending=False)
    top = fi.head(top_n)

    fig, ax = plt.subplots(figsize=(10, max(4, top_n // 3)))
    top[::-1].plot(kind="barh", ax=ax, color="#4C72B0", edgecolor="none")
    ax.set_xlabel("Важность признака (%)", fontsize=11)
    ax.set_title(f"TOP-{top_n} Feature Importance — {prefix}", fontsize=13)
    ax.tick_params(axis="y", labelsize=9)
    ax.grid(axis="x", alpha=0.3)
    plt.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_feature_importance.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log(f"[report] Feature importance → {path}")
    return path


# ---------------------------------------------------------------------------
# Actual vs Predicted
# ---------------------------------------------------------------------------

def plot_actual_vs_predicted(
    y_true: "np.ndarray | pd.Series",
    y_pred: np.ndarray,
    timestamps: "pd.Series | None" = None,
    *,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Строит временно́й график actual vs predicted и сохраняет PNG."""
    if not _HAS_MATPLOTLIB:
        raise ImportError("Установи matplotlib: pip install matplotlib")

    y_true_arr = np.asarray(y_true)
    x = timestamps.values if timestamps is not None else np.arange(len(y_true_arr))

    fig, ax = plt.subplots(figsize=(16, 4))
    ax.plot(x, y_true_arr, label="Actual",    alpha=0.75, linewidth=0.9, color="#2196F3")
    ax.plot(x, y_pred,     label="Predicted", alpha=0.75, linewidth=0.9, color="#F44336")
    ax.axhline(0.0, color="gray", linewidth=0.6, linestyle="--")
    ax.set_ylabel("target_return_1", fontsize=11)
    ax.set_title(f"Actual vs Predicted — {prefix}", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(alpha=0.25)
    plt.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_actual_vs_predicted.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log(f"[report] Actual vs Predicted → {path}")
    return path


# ---------------------------------------------------------------------------
# Cumulative P&L curve
# ---------------------------------------------------------------------------

def plot_cumulative_pnl(
    y_true: "np.ndarray | pd.Series",
    y_pred: np.ndarray,
    timestamps: "pd.Series | None" = None,
    *,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Строит график Cumulative P&L (накопленный доход стратегии long/short) и сохраняет PNG.

    Стратегия: sign(pred) * actual на каждом шаге.
    Добавляется линия Buy&Hold (накопленный actual) для сравнения.
    """
    if not _HAS_MATPLOTLIB:
        raise ImportError("Установи matplotlib: pip install matplotlib")

    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)
    x = timestamps.values if timestamps is not None else np.arange(len(y_true_arr))

    strategy_ret  = np.sign(y_pred_arr) * y_true_arr
    cum_strategy  = np.cumsum(strategy_ret)
    cum_bh        = np.cumsum(y_true_arr)

    fig, ax = plt.subplots(figsize=(16, 4))
    ax.plot(x, cum_strategy, label="Strategy (sign pred)", linewidth=1.2, color="#4CAF50")
    ax.plot(x, cum_bh,       label="Buy & Hold",           linewidth=1.0, color="#2196F3", alpha=0.7, linestyle="--")
    ax.axhline(0.0, color="gray", linewidth=0.6, linestyle=":")
    ax.set_ylabel("Cumulative return", fontsize=11)
    ax.set_title(f"Cumulative P&L — {prefix}", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(alpha=0.25)
    plt.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_cumulative_pnl.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log(f"[report] Cumulative P&L → {path}")
    return path


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
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_grid_results.csv"
    grid_df.to_csv(path, index=False)
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
        print(f"  {key:<16}: {value:.6f}")
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
        "saved_at":  _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
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


# ---------------------------------------------------------------------------
# Сохранение / загрузка лучших параметров Grid Search (per-dataset)
# ---------------------------------------------------------------------------

def save_grid_best_params(
    best_params: dict,
    best_row: dict,
    *,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Сохраняет лучшие параметры Grid Search (топ-1 по Sharpe) в JSON-файл.

    Файл называется {prefix}_grid_best.json и перезаписывается при каждом
    новом запуске Grid Search для данного датасета.
    """
    import datetime as _dt
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_grid_best.json"
    payload = {
        "prefix":      prefix,
        "saved_at":    _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "best_params": best_params,
        "best_metrics": {
            k: best_row[k]
            for k in (
                "mean_rmse_cv", "std_rmse_cv", "sharpe",
                "dir_acc_pct", "mae_pct", "profit_factor",
                "accuracy", "elapsed_s",
            )
            if k in best_row
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[report] Grid best params → {path}")
    return path


def load_grid_best_params(
    prefix: str,
    *,
    models_dir: Path = MODELS_DIR,
) -> dict | None:
    """Загружает ранее сохранённые лучшие параметры Grid Search для датасета.

    Возвращает словарь с ключами 'best_params', 'best_metrics', 'saved_at'
    или None, если файл не найден.
    """
    path = models_dir / f"{prefix}_grid_best.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Автосохранение / загрузка результатов сессии (для восстановления после
# перезагрузки страницы — st.session_state сбрасывается при F5)
# ---------------------------------------------------------------------------

def _to_json_safe(obj: object) -> object:
    """Рекурсивно конвертирует numpy-типы в Python-примитивы для JSON-сериализации."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_safe(x) for x in obj]
    return obj


def save_session_result(
    model: object,
    metrics: dict,
    y_pred: "np.ndarray",
    y_test: "pd.Series",
    ts_test: "pd.Series",
    feature_cols: list[str],
    best_params: dict,
    overfit_diagnostics: "dict | None",
    *,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> None:
    """Автосохраняет результаты обучения на диск для восстановления после перезагрузки.

    Сохраняет три артефакта:
      {prefix}_session.cbm         — веса CatBoost-модели
      {prefix}_session_arrays.npz  — y_pred, y_test, ts_test
      {prefix}_session.json        — метрики, гиперпараметры, overfit_diagnostics
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Модель
    model.save_model(str(output_dir / f"{prefix}_session.cbm"))  # type: ignore[attr-defined]

    # 2. Числовые массивы
    y_pred_arr = np.asarray(y_pred, dtype=float)
    y_test_arr = np.asarray(y_test, dtype=float)
    if pd.api.types.is_datetime64_any_dtype(ts_test):
        # Pandas 2.0+ может хранить datetime64[us, UTC] вместо datetime64[ns, UTC].
        # Определяем фактическую единицу из dtype-строки, чтобы при загрузке
        # передать правильный unit= в pd.to_datetime и не получить 1970-е годы.
        _dtype_str = str(getattr(ts_test, "dtype", ""))
        if "[us" in _dtype_str:
            ts_unit = "us"
        elif "[ms" in _dtype_str:
            ts_unit = "ms"
        else:
            ts_unit = "ns"
        ts_arr  = ts_test.astype("int64").values
        _LOG.info("[report] save_session: ts dtype=%s → unit=%s", _dtype_str, ts_unit)
    else:
        ts_arr  = np.asarray(ts_test, dtype="int64")
        ts_unit = "idx"
        _LOG.info("[report] save_session: ts non-datetime → unit=idx")
    _LOG.info(
        "[report] save_session: y_pred shape=%s y_test shape=%s ts_arr shape=%s",
        y_pred_arr.shape, y_test_arr.shape, ts_arr.shape,
    )
    np.savez_compressed(
        output_dir / f"{prefix}_session_arrays.npz",
        y_pred=y_pred_arr,
        y_test=y_test_arr,
        ts_test=ts_arr,
    )

    # 3. Метаданные
    payload = {
        "prefix":              prefix,
        "metrics":             _to_json_safe(metrics),
        "best_params":         _to_json_safe(best_params),
        "feature_cols":        list(feature_cols),
        "overfit_diagnostics": _to_json_safe(overfit_diagnostics),
        "ts_unit":             ts_unit,
    }
    (output_dir / f"{prefix}_session.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log(f"[report] Сессия сохранена → {output_dir}/{prefix}_session.*")


def load_session_result(
    prefix: str,
    *,
    models_dir: Path = MODELS_DIR,
) -> "dict | None":
    """Загружает автосохранённые результаты обучения с диска.

    Возвращает словарь, совместимый с model_result в model_page.py,
    или None если файлы отсутствуют или повреждены.
    """
    cbm_path  = models_dir / f"{prefix}_session.cbm"
    json_path = models_dir / f"{prefix}_session.json"
    npz_path  = models_dir / f"{prefix}_session_arrays.npz"

    if not (cbm_path.exists() and json_path.exists() and npz_path.exists()):
        return None
    try:
        from catboost import CatBoostRegressor  # lazy import
        model = CatBoostRegressor()
        model.load_model(str(cbm_path))

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        arrs    = np.load(str(npz_path), allow_pickle=False)

        y_pred = arrs["y_pred"]
        y_test = pd.Series(arrs["y_test"], name="target_return_1")

        ts_unit = payload.get("ts_unit", "ns")
        ts_arr  = arrs["ts_test"]
        if ts_unit == "ns":
            ts_test = pd.Series(pd.to_datetime(ts_arr, unit="ns", utc=True))
        else:
            ts_test = pd.Series(ts_arr)

        return {
            "model":               model,
            "metrics":             payload["metrics"],
            "y_pred":              y_pred,
            "y_test":              y_test,
            "ts_test":             ts_test,
            "feature_cols":        payload["feature_cols"],
            "best_params":         payload["best_params"],
            "grid_df":             None,
            "prefix":              payload["prefix"],
            "overfit_diagnostics": payload.get("overfit_diagnostics"),
        }
    except Exception:
        return None


def load_grid_session_result(
    prefix: str,
    *,
    models_dir: Path = MODELS_DIR,
) -> "dict | None":
    """Загружает сохранённые результаты Grid Search с диска.

    Читает {prefix}_grid_results.csv и {prefix}_grid_best.json.
    Возвращает словарь, совместимый с grid_result в model_page.py, или None.
    """
    csv_path  = models_dir / f"{prefix}_grid_results.csv"
    best_path = models_dir / f"{prefix}_grid_best.json"

    if not (csv_path.exists() and best_path.exists()):
        return None
    try:
        grid_df     = pd.read_csv(str(csv_path))
        best        = json.loads(best_path.read_text(encoding="utf-8"))
        best_params = best.get("best_params", {})
        if not best_params or grid_df.empty:
            return None
        return {
            "grid_df":     grid_df,
            "best_params": best_params,
            "prefix":      prefix,
        }
    except Exception:
        return None
