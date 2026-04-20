"""Фоновый менеджер обучения CatBoost в отдельных потоках.

Позволяет перезагружать страницу Streamlit без прерывания обучения.
Прогресс и статус сохраняются в JSON-файлы на диске; UI читает их при
каждом рендере и обновляет страницу автоматически.

Статус-файлы (в MODELS_DIR):
  {prefix}_grid_status.json   — ход Grid Search
  {prefix}_optuna_status.json — ход Optuna-поиска
  {prefix}_train_status.json  — ход финального обучения / пайплайна
"""
from __future__ import annotations

import json
import logging
import threading
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.utils import now_utc as _now, to_json_safe as _safe

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Глобальный реестр потоков.
# Живёт на уровне модуля Python-процесса — переживает Streamlit page reload
# (но не перезапуск Streamlit-сервера, в этом случае потоки завершаются).
# ---------------------------------------------------------------------------
_THREADS: dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


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
    except OSError as exc:
        _LOG.warning("[trainer] Не удалось записать статус %s/%s: %s", prefix, task, exc)


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
    annualize_factor: float = 1.0,
    step_ms: int = 3_600_000,
    cv_mode: str = "expanding",
    max_train_size: int | None = None,
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
        history: list[dict] = []

        def _cb(idx: int, total_: int, row: dict) -> None:
            live.append(row)
            best = min(
                live,
                key=lambda r: (-float(r.get("sharpe", 0)), float(r.get("mean_rmse_cv", 1e9))),
            )
            history.append({
                "idx":           int(idx),
                "sharpe":        float(row.get("sharpe", 0)),
                "mean_rmse_cv":  float(row.get("mean_rmse_cv", 0)),
                "dir_acc_pct":   float(row.get("dir_acc_pct", 0)),
                "profit_factor": float(row.get("profit_factor", 0)),
                "best_sharpe":   float(best.get("sharpe", 0)),
            })
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
                "history":     history,
                "started_at":  started,
                "finished_at": None,
                "error_msg":   None,
            })

        try:
            # Горизонт прогноза в барах = purge gap между train/val фолдами
            _target_bars = max(1, round(10_800_000 / step_ms))
            _LOG.info(
                "[trainer] Grid Search start: prefix=%s total=%d use_gpu=%s "
                "annualize=%.1f target_bars=%d",
                prefix, total, use_gpu, annualize_factor, _target_bars,
            )
            best_params, grid_df = grid_search_cv(
                X_tr, y_tr,
                use_gpu=use_gpu,
                param_grid=custom_pg,
                on_combo_done=_cb,
                annualize_factor=annualize_factor,
                target_horizon_bars=_target_bars,
                cv_mode=cv_mode,
                max_train_size=max_train_size,
            )
            _LOG.info("[trainer] Grid Search done: best_params=%s", best_params)
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
            tb = traceback.format_exc()
            _LOG.error("[trainer] Grid Search FAILED: %s\n%s", exc, tb)
            _write_status(models_dir, prefix, "grid", {
                "status":      "error",
                "error_msg":   f"{type(exc).__name__}: {exc}",
                "traceback":   tb,
                "started_at":  started,
                "finished_at": _now(),
            })

    t = threading.Thread(target=_task, daemon=False, name=key)
    with _LOCK:
        _THREADS[key] = t
    t.start()
    return True


# ---------------------------------------------------------------------------
# Optuna-поиск в фоне
# ---------------------------------------------------------------------------

def start_optuna_search(
    prefix: str,
    X_tr: pd.DataFrame,
    y_tr: pd.Series,
    *,
    n_trials: int,
    use_gpu: bool,
    models_dir: Path,
    annualize_factor: float = 1.0,
    step_ms: int = 3_600_000,
    search_space: "dict | None" = None,
    cv_mode: str = "expanding",
    max_train_size: int | None = None,
) -> bool:
    """Запускает Optuna TPE-поиск в фоновом потоке.

    Возвращает True если поток успешно запущен;
    False если Optuna-поиск для этого prefix уже выполняется.
    """
    key = f"{prefix}_optuna"
    with _LOCK:
        if key in _THREADS and _THREADS[key].is_alive():
            return False

    def _task() -> None:
        from backend.model import optuna_search_cv
        from backend.model.report import save_optuna_best_params, save_optuna_results

        started = _now()
        _write_status(models_dir, prefix, "optuna", {
            "status":      "running",
            "total":       n_trials,
            "current":     0,
            "best_so_far": {},
            "started_at":  started,
            "finished_at": None,
            "error_msg":   None,
        })

        live: list[dict] = []
        history: list[dict] = []

        def _cb(idx: int, total_: int, row: dict) -> None:
            live.append(row)
            best = min(
                live,
                key=lambda r: (-float(r.get("sharpe", 0)), float(r.get("mean_rmse_cv", 1e9))),
            )
            history.append({
                "idx":           int(idx),
                "sharpe":        float(row.get("sharpe", 0)),
                "mean_rmse_cv":  float(row.get("mean_rmse_cv", 0)),
                "dir_acc_pct":   float(row.get("dir_acc_pct", 0)),
                "profit_factor": float(row.get("profit_factor", 0)),
                "best_sharpe":   float(best.get("sharpe", 0)),
            })
            _write_status(models_dir, prefix, "optuna", {
                "status":   "running",
                "total":    total_,
                "current":  idx,
                "best_so_far": {
                    "sharpe":        float(best.get("sharpe", 0)),
                    "mean_rmse_cv":  float(best.get("mean_rmse_cv", 0)),
                    "dir_acc_pct":   float(best.get("dir_acc_pct", 0)),
                    "profit_factor": float(best.get("profit_factor", 0)),
                },
                "history":     history,
                "started_at":  started,
                "finished_at": None,
                "error_msg":   None,
            })

        try:
            _target_bars = max(1, round(10_800_000 / step_ms))
            _LOG.info(
                "[trainer] Optuna start: prefix=%s n_trials=%d use_gpu=%s "
                "annualize=%.1f target_bars=%d",
                prefix, n_trials, use_gpu, annualize_factor, _target_bars,
            )
            best_params, trials_df = optuna_search_cv(
                X_tr, y_tr,
                n_trials=n_trials,
                use_gpu=use_gpu,
                search_space=search_space,
                on_trial_done=_cb,
                annualize_factor=annualize_factor,
                target_horizon_bars=_target_bars,
                cv_mode=cv_mode,
                max_train_size=max_train_size,
            )
            _LOG.info("[trainer] Optuna done: best_params=%s", best_params)
            save_optuna_results(trials_df, prefix=prefix)
            save_optuna_best_params(best_params, trials_df.iloc[0].to_dict(), prefix=prefix)
            best_row = trials_df.iloc[0]
            _write_status(models_dir, prefix, "optuna", {
                "status":  "done",
                "total":   n_trials,
                "current": n_trials,
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
            tb = traceback.format_exc()
            _LOG.error("[trainer] Optuna FAILED: %s\n%s", exc, tb)
            _write_status(models_dir, prefix, "optuna", {
                "status":      "error",
                "error_msg":   f"{type(exc).__name__}: {exc}",
                "traceback":   tb,
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
    target_col: "str | None" = None,
    cv_mode: str = "expanding",
    max_train_size: int | None = None,
    mlflow_enabled: bool = False,
    mlflow_uri: str = "http://localhost:5000",
    mlflow_experiment: str = "ModelLine",
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
            save_model,
            train_final_model,
        )
        from backend.model.report import (
            register_model_version,
            save_grid_results,
            save_predictions_json,
            save_session_result,
        )

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
                _LOG.info("[trainer] Train pipeline: \u043f\u0440\u043e\u043f\u0443\u0441\u043a \u0433\u0440\u0438\u0434\u0430, params=%s", best_params)
            else:
                # Grid Search внутри пайплайна
                _target_bars = max(1, round(10_800_000 / step_ms))
                _LOG.info(
                    "[trainer] Train pipeline: Grid Search start target_bars=%d", _target_bars
                )
                best_params, grid_df_inner = grid_search_cv(
                    X_train, y_train,
                    use_gpu=use_gpu,
                    param_grid=param_grid,
                    annualize_factor=annualize_factor,
                    target_horizon_bars=_target_bars,
                    cv_mode=cv_mode,
                    max_train_size=max_train_size,
                )
                _LOG.info("[trainer] Train pipeline: Grid Search done, best=%s", best_params)
                save_grid_results(grid_df_inner, prefix=prefix)
                # Переходим к фазе финального обучения
                _write_status(models_dir, prefix, "train", {
                    "status":      "running",
                    "phase":       "train",
                    "started_at":  started,
                    "finished_at": None,
                    "error_msg":   None,
                })

            _LOG.info(
                "[trainer] \u0424\u0438\u043d\u0430\u043b\u044c\u043d\u043e\u0435 \u043e\u0431\u0443\u0447\u0435\u043d\u0438\u0435: train=%d test=%d features=%d annualize=%.1f",
                len(X_train), len(X_test), len(feature_cols), annualize_factor,
            )
            model, metrics, y_pred = train_final_model(
                X_train, y_train,
                X_test,  y_test,
                best_params,
                annualize_factor=annualize_factor,
                use_gpu=use_gpu,
            )
            _LOG.info("[trainer] \u041e\u0431\u0443\u0447\u0435\u043d\u0438\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u043e: %s", metrics)

            symbol = prefix.rsplit("_", 1)[0]
            timeframe = prefix.rsplit("_", 1)[1]
            save_model(model, symbol, timeframe, models_dir=models_dir)

            try:
                overfit_diag = compute_overfitting_diagnostics(
                    model, X_train, y_train, X_test, y_test,
                    feature_cols=feature_cols,
                    step_ms=step_ms,
                )
            except Exception as _diag_exc:
                _LOG.warning(
                    "[trainer] \u0414\u0438\u0430\u0433\u043d\u043e\u0441\u0442\u0438\u043a\u0430 \u043f\u0435\u0440\u0435\u043e\u0431\u0443\u0447\u0435\u043d\u0438\u044f \u043d\u0435 \u0443\u0434\u0430\u043b\u0430\u0441\u044c: %s\n%s",
                    _diag_exc, traceback.format_exc(),
                )
                overfit_diag = None

            # Сохраняем результаты на диск — переживут перезагрузку страницы
            save_session_result(
                model, metrics, y_pred, y_test, ts_test,
                feature_cols, best_params, overfit_diag,
                prefix=prefix,
                target_col=target_col,
            )

            # Сохраняем все предсказания в JSON
            save_predictions_json(
                y_test, y_pred, ts_test,
                metrics=metrics,
                best_params=best_params,
                prefix=prefix,
                output_dir=models_dir,
            )

            # MLflow logging
            from backend.model.mlflow_utils import log_session_to_mlflow
            _sym = prefix.rsplit("_", 1)[0]
            _tf  = prefix.rsplit("_", 1)[1]
            _cbm_path = models_dir / f"{_sym}_{_tf}.cbm"
            mlflow_run_id = log_session_to_mlflow(
                enabled=mlflow_enabled,
                tracking_uri=mlflow_uri,
                experiment_name=mlflow_experiment,
                run_name=prefix,
                params=best_params,
                metrics=metrics,
                feature_cols=feature_cols,
                model_path=_cbm_path,
                tags={
                    "symbol":    _sym,
                    "timeframe": _tf,
                    "target":    target_col or "target_return_1",
                },
            )

            # Registry — записываем версию с уже известным mlflow_run_id
            register_model_version(
                prefix,
                metrics,
                best_params,
                feature_cols,
                models_dir=models_dir,
                mlflow_run_id=mlflow_run_id,
                target_col=target_col,
                n_train=len(X_train),
                n_test=len(X_test),
            )

            _write_status(models_dir, prefix, "train", {
                "status":        "done",
                "phase":         "train",
                "metrics":       metrics,
                "mlflow_run_id": mlflow_run_id,
                "started_at":    started,
                "finished_at":   _now(),
                "error_msg":     None,
            })

        except Exception as exc:
            tb = traceback.format_exc()
            _LOG.error("[trainer] Train pipeline FAILED: %s\n%s", exc, tb)
            _write_status(models_dir, prefix, "train", {
                "status":      "error",
                "phase":       initial_phase,
                "error_msg":   f"{type(exc).__name__}: {exc}",
                "traceback":   tb,
                "started_at":  started,
                "finished_at": _now(),
            })

    t = threading.Thread(target=_task, daemon=False, name=key)
    with _LOCK:
        _THREADS[key] = t
    t.start()
    return True
