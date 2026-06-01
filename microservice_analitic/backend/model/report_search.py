"""Сохранение/загрузка результатов сессий, Grid Search и Optuna-поиска.

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
        "saved_at":    _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
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
    target_col: "str | None" = None,
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
        "target_col":          target_col,
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
        y_test = pd.Series(
            arrs["y_test"],
            name=payload.get("target_col") or "target_return_1",
        )

        ts_unit = payload.get("ts_unit", "ns")
        ts_arr  = arrs["ts_test"]
        if ts_unit in ("ns", "us", "ms"):
            ts_test = pd.Series(pd.to_datetime(ts_arr, unit=ts_unit, utc=True))
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
            "target_col":          payload.get("target_col"),
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
        from backend.csv_io import load_csv, CsvLoadError
        grid_df = load_csv(csv_path, missing_ok=False)
        if grid_df is None:
            return None
        best        = json.loads(best_path.read_text(encoding="utf-8"))
        best_params = best.get("best_params", {})
        if not best_params or grid_df.empty:
            return None
        return {
            "grid_df":     grid_df,
            "best_params": best_params,
            "prefix":      prefix,
        }
    except (CsvLoadError, json.JSONDecodeError, FileNotFoundError):
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Результаты Optuna-поиска (аналогично Grid Search, но отдельные файлы)
# ---------------------------------------------------------------------------

def save_optuna_results(
    trials_df: pd.DataFrame,
    *,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Сохраняет таблицу Optuna-трайлов в CSV ({prefix}_optuna_results.csv)."""
    from backend.csv_io import save_csv
    path = save_csv(trials_df, output_dir / f"{prefix}_optuna_results.csv")
    log(f"[report] Optuna results → {path}")
    return path


def save_optuna_best_params(
    best_params: dict,
    best_row: dict,
    *,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Сохраняет лучшие параметры Optuna-поиска (топ-1 по Sharpe) в JSON.

    Файл называется {prefix}_optuna_best.json. Формат совместим с
    save_grid_best_params: ключи prefix, saved_at, best_params, best_metrics.
    """
    import datetime as _dt
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_optuna_best.json"
    payload = {
        "prefix":      prefix,
        "saved_at":    _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "best_params": _to_json_safe(best_params),
        "best_metrics": _to_json_safe({
            k: best_row[k]
            for k in ("sharpe", "mean_rmse_cv", "dir_acc_pct", "profit_factor")
            if k in best_row
        }),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[report] Optuna best params → {path}")
    return path


def load_optuna_best_params(
    prefix: str,
    *,
    models_dir: Path = MODELS_DIR,
) -> "dict | None":
    """Загружает ранее сохранённые лучшие параметры Optuna для датасета.

    Возвращает словарь с ключами 'best_params', 'best_metrics', 'saved_at'
    или None, если файл не найден.
    """
    path = models_dir / f"{prefix}_optuna_best.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_optuna_session_result(
    prefix: str,
    *,
    models_dir: Path = MODELS_DIR,
) -> "dict | None":
    """Загружает сохранённые результаты Optuna-поиска с диска.

    Читает {prefix}_optuna_results.csv и {prefix}_optuna_best.json.
    Возвращает словарь совместимый с grid_result (ключи grid_df, best_params, prefix).
    """
    csv_path  = models_dir / f"{prefix}_optuna_results.csv"
    best_path = models_dir / f"{prefix}_optuna_best.json"

    if not (csv_path.exists() and best_path.exists()):
        return None
    try:
        from backend.csv_io import load_csv, CsvLoadError
        trials_df = load_csv(csv_path, missing_ok=False)
        if trials_df is None:
            return None
        best        = json.loads(best_path.read_text(encoding="utf-8"))
        best_params = best.get("best_params", {})
        if not best_params or trials_df.empty:
            return None
        return {
            "grid_df":     trials_df,
            "best_params": best_params,
            "prefix":      prefix,
        }
    except (CsvLoadError, json.JSONDecodeError, FileNotFoundError):
        return None
    except Exception:
        return None
