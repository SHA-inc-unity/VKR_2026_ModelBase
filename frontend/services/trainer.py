"""Фоновый менеджер обучения CatBoost в отдельных потоках.

Позволяет перезагружать страницу Streamlit без прерывания обучения.
Прогресс и статус сохраняются в JSON-файлы на диске; UI читает их при
каждом рендере и обновляет страницу автоматически.

Статус-файлы (в MODELS_DIR):
  {prefix}_grid_status.json  — ход Grid Search
  {prefix}_train_status.json — ход финального обучения / пайплайна
"""
from __future__ import annotations

import datetime
import json
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Глобальный реестр потоков.
# Живёт на уровне модуля Python-процесса — переживает Streamlit page reload
# (но не перезапуск Streamlit-сервера, в этом случае потоки завершаются).
# ---------------------------------------------------------------------------
_THREADS: dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _safe(obj: Any) -> Any:
    """Рекурсивно конвертирует numpy/pandas типы в JSON-сериализуемые."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe(x) for x in obj]
    return obj


# ---------------------------------------------------------------------------
# Операции со статус-файлами
# ---------------------------------------------------------------------------

def _status_path(models_dir: Path, prefix: str, task: str) -> Path:
    return models_dir / f"{prefix}_{task}_status.json"


def _write_status(models_dir: Path, prefix: str, task: str, payload: dict) -> None:
    """Атомарно записывает статус в JSON-файл (игнорирует ошибки ввода-вывода)."""
    models_dir.mkdir(parents=True, exist_ok=True)
    try:
        _status_path(models_dir, prefix, task).write_text(
            json.dumps(_safe(payload), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def read_status(models_dir: Path, prefix: str, task: str) -> dict | None:
    """Читает статус-файл. Возвращает None если файл не существует."""
    p = _status_path(models_dir, prefix, task)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_status(models_dir: Path, prefix: str, task: str) -> None:
    """Удаляет статус-файл (например, при сбросе состояния)."""
    try:
        _status_path(models_dir, prefix, task).unlink(missing_ok=True)
    except OSError:
        pass


def is_thread_alive(key: str) -> bool:
    """Проверяет, выполняется ли фоновый поток с данным ключом."""
    with _LOCK:
        t = _THREADS.get(key)
    return t is not None and t.is_alive()


# ---------------------------------------------------------------------------
# Grid Search в фоне
# ---------------------------------------------------------------------------

def start_grid_search(
    prefix: str,
    X_tr: pd.DataFrame,
    y_tr: pd.Series,
    custom_pg: list[dict],
    *,
    use_gpu: bool,
    models_dir: Path,
) -> bool:
    """Запускает Grid Search в фоновом потоке.

    Возвращает True если поток успешно запущен;
    False если Grid Search для этого prefix уже выполняется.
    """
    key = f"{prefix}_grid"
    with _LOCK:
        if key in _THREADS and _THREADS[key].is_alive():
            return False

    def _task() -> None:
        from backend.model import grid_search_cv
        from backend.model.report import save_grid_best_params, save_grid_results

        total = len(custom_pg)
        started = _now()
        _write_status(models_dir, prefix, "grid", {
            "status":     "running",
            "total":      total,
            "current":    0,
            "best_so_far": {},
            "started_at":  started,
            "finished_at": None,
            "error_msg":   None,
        })

        live: list[dict] = []

        def _cb(idx: int, total_: int, row: dict) -> None:
            live.append(row)
            best = min(
                live,
                key=lambda r: (-float(r.get("sharpe", 0)), float(r.get("mean_rmse_cv", 1e9))),
            )
            _write_status(models_dir, prefix, "grid", {
                "status":   "running",
                "total":    total_,
                "current":  idx,
                "best_so_far": {
                    "sharpe":        float(best.get("sharpe", 0)),
                    "mean_rmse_cv":  float(best.get("mean_rmse_cv", 0)),
                    "dir_acc_pct":   float(best.get("dir_acc_pct", 0)),
                    "profit_factor": float(best.get("profit_factor", 0)),
                },
                "started_at":  started,
                "finished_at": None,
                "error_msg":   None,
            })

        try:
            best_params, grid_df = grid_search_cv(
                X_tr, y_tr,
                use_gpu=use_gpu,
                param_grid=custom_pg,
                on_combo_done=_cb,
            )
            save_grid_results(grid_df, prefix=prefix)
            save_grid_best_params(best_params, grid_df.iloc[0].to_dict(), prefix=prefix)
            best_row = grid_df.iloc[0]
            _write_status(models_dir, prefix, "grid", {
                "status":  "done",
                "total":   total,
                "current": total,
                "best_params": best_params,
                "best_metrics": {
                    k: best_row[k]
                    for k in ("sharpe", "mean_rmse_cv", "dir_acc_pct", "profit_factor")
                    if k in best_row
                },
                "started_at":  started,
                "finished_at": _now(),
                "error_msg":   None,
            })
        except Exception as exc:
            _write_status(models_dir, prefix, "grid", {
                "status":      "error",
                "error_msg":   str(exc),
                "started_at":  started,
                "finished_at": _now(),
            })

    t = threading.Thread(target=_task, daemon=False, name=key)
    with _LOCK:
        _THREADS[key] = t
    t.start()
    return True


# ---------------------------------------------------------------------------
# Пайплайн финального обучения в фоне
# (опционально включает Grid Search если prior_params не задан)
# ---------------------------------------------------------------------------

def start_training_pipeline(
    prefix: str,
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    ts_test: pd.Series,
    feature_cols: list[str],
    *,
    prior_params: "dict | None",
    param_grid: "list[dict] | None",
    use_gpu: bool,
    annualize_factor: float,
    step_ms: int,
    models_dir: Path,
) -> bool:
    """Запускает пайплайн обучения в фоновом потоке.

    Если prior_params не None — Grid Search пропускается, используются
    переданные параметры напрямую.
    Если prior_params None и param_grid не None — сначала выполняется
    Grid Search, затем финальное обучение.

    Возвращает True если поток успешно запущен; False если уже выполняется.
    """
    key = f"{prefix}_train"
    with _LOCK:
        if key in _THREADS and _THREADS[key].is_alive():
            return False

    def _task() -> None:
        from backend.model import (
            compute_overfitting_diagnostics,
            grid_search_cv,
            train_final_model,
        )
        from backend.model.report import save_grid_results, save_session_result

        started = _now()
        initial_phase = "train" if prior_params is not None else "grid"
        _write_status(models_dir, prefix, "train", {
            "status":      "running",
            "phase":       initial_phase,
            "started_at":  started,
            "finished_at": None,
            "error_msg":   None,
        })

        try:
            if prior_params is not None:
                best_params = prior_params
            else:
                # Grid Search внутри пайплайна
                best_params, grid_df_inner = grid_search_cv(
                    X_train, y_train,
                    use_gpu=use_gpu,
                    param_grid=param_grid,
                )
                save_grid_results(grid_df_inner, prefix=prefix)
                # Переходим к фазе финального обучения
                _write_status(models_dir, prefix, "train", {
                    "status":      "running",
                    "phase":       "train",
                    "started_at":  started,
                    "finished_at": None,
                    "error_msg":   None,
                })

            model, metrics, y_pred = train_final_model(
                X_train, y_train,
                X_test,  y_test,
                best_params,
                annualize_factor=annualize_factor,
                use_gpu=use_gpu,
            )

            try:
                overfit_diag = compute_overfitting_diagnostics(
                    model, X_train, y_train, X_test, y_test,
                    feature_cols=feature_cols,
                    step_ms=step_ms,
                )
            except Exception:
                overfit_diag = None

            # Сохраняем результаты на диск — переживут перезагрузку страницы
            save_session_result(
                model, metrics, y_pred, y_test, ts_test,
                feature_cols, best_params, overfit_diag,
                prefix=prefix,
            )

            _write_status(models_dir, prefix, "train", {
                "status":      "done",
                "phase":       "train",
                "metrics":     metrics,
                "started_at":  started,
                "finished_at": _now(),
                "error_msg":   None,
            })

        except Exception as exc:
            _write_status(models_dir, prefix, "train", {
                "status":      "error",
                "phase":       initial_phase,
                "error_msg":   str(exc),
                "started_at":  started,
                "finished_at": _now(),
            })

    t = threading.Thread(target=_task, daemon=False, name=key)
    with _LOCK:
        _THREADS[key] = t
    t.start()
    return True
