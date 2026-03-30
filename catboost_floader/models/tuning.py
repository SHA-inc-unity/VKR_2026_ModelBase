from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.model_selection import ParameterGrid, TimeSeriesSplit
from tqdm import tqdm

from catboost_floader.core.config import (
    apply_cpu_worker_limits,
    current_worker_thread_count,
    DIRECT_CATBOOST_PARAMS,
    DIRECT_SEARCH_GRID,
    ENABLE_PARALLEL_CPU_FULL_EVALUATION,
    ENABLE_GPU_FULL_EVALUATION,
    HALVING_FACTOR,
    HALVING_MAX_ROWS,
    is_nested_outer_parallel,
    PARALLEL_EVAL_WORKERS,
    RANDOM_SEED,
    RANGE_HIGH_CATBOOST_PARAMS,
    RANGE_LOW_CATBOOST_PARAMS,
    RANGE_SEARCH_GRID,
    REPORT_DIR,
    TIME_SERIES_SPLITS,
    resolve_parallel_cpu_settings,
)
from catboost_floader.core.utils import get_logger

logger = get_logger("hyperparameter_search")


def _initialize_cpu_parallel_worker(thread_count: int) -> None:
    apply_cpu_worker_limits(thread_count)


def _make_estimator(params: Dict[str, Any], *, thread_count: int | None = None) -> CatBoostRegressor:
    model_params = dict(params)
    model_params.setdefault("random_seed", RANDOM_SEED)
    model_params.setdefault("verbose", False)
    model_params.pop("task_type", None)
    model_params.pop("devices", None)
    model_params.pop("gpu_ram_part", None)
    if thread_count is not None:
        model_params["thread_count"] = max(1, int(thread_count))
    return CatBoostRegressor(**model_params)


def _sanitize_search_grid(search_grid: Dict[str, Any]) -> Dict[str, List[Any]]:
    clean_grid: Dict[str, List[Any]] = {}
    for key, value in search_grid.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            clean_grid[key] = list(value)
        else:
            clean_grid[key] = [value]
    return clean_grid


def _prepare_X_y(X: pd.DataFrame, y: pd.Series) -> Tuple[pd.DataFrame, pd.Series]:
    X2 = X.copy().reset_index(drop=True)
    y2 = pd.Series(y).reset_index(drop=True)

    drop_cols = []
    for col in X2.columns:
        dtype_str = str(X2[col].dtype)
        if dtype_str in {"object", "string"} or "datetime" in dtype_str:
            drop_cols.append(col)
    if drop_cols:
        X2 = X2.drop(columns=drop_cols, errors="ignore")

    X2 = X2.replace([np.inf, -np.inf], np.nan)
    valid_mask = X2.notna().all(axis=1) & y2.notna()
    X2 = X2.loc[valid_mask].reset_index(drop=True)
    y2 = y2.loc[valid_mask].reset_index(drop=True)

    if not X2.empty:
        nunique = X2.nunique(dropna=False)
        constant_cols = nunique[nunique <= 1].index.tolist()
        if constant_cols:
            X2 = X2.drop(columns=constant_cols, errors="ignore")

    if HALVING_MAX_ROWS and len(X2) > HALVING_MAX_ROWS:
        X2 = X2.iloc[-HALVING_MAX_ROWS:].reset_index(drop=True)
        y2 = y2.iloc[-HALVING_MAX_ROWS:].reset_index(drop=True)

    return X2, y2


def _score_predictions(y_true: np.ndarray, y_pred: np.ndarray, scorer: str) -> float:
    if scorer == "neg_mean_absolute_error":
        return -float(np.mean(np.abs(y_true - y_pred)))
    if scorer == "neg_mean_squared_error":
        return -float(np.mean((y_true - y_pred) ** 2))
    raise ValueError(f"Unsupported scorer: {scorer}")


def _evaluate_params_cv(
    params: Dict[str, Any],
    X: pd.DataFrame,
    y: pd.Series,
    scorer: str,
    n_splits: int,
) -> Tuple[float, List[float]]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_scores: List[float] = []

    for train_idx, val_idx in tscv.split(X):
        X_train = X.iloc[train_idx]
        y_train = y.iloc[train_idx]
        X_val = X.iloc[val_idx]
        y_val = y.iloc[val_idx]

        if X_train.empty or X_val.empty or y_train.empty or y_val.empty:
            continue

        model = _make_estimator(params)
        model.fit(X_train, y_train)

        preds = model.predict(X_val)
        score = _score_predictions(y_val.to_numpy(), np.asarray(preds), scorer)
        fold_scores.append(score)

    if not fold_scores:
        return -np.inf, []

    return float(np.mean(fold_scores)), fold_scores


def _evaluate_stage2_candidate(
    params: Dict[str, Any],
    X_stage2: pd.DataFrame,
    y_stage2: pd.Series,
    folds: int,
    *,
    search_name: str,
    scorer: str,
    thread_count: int | None = None,
) -> float:
    tscv = TimeSeriesSplit(n_splits=folds)
    fold_scores: List[float] = []
    effective_threads = thread_count or current_worker_thread_count()

    for fold_idx, (train_idx, val_idx) in enumerate(tscv.split(X_stage2), start=1):
        X_train = X_stage2.iloc[train_idx]
        y_train = y_stage2.iloc[train_idx]
        X_val = X_stage2.iloc[val_idx]
        y_val = y_stage2.iloc[val_idx]

        if X_train.empty or X_val.empty or y_train.empty or y_val.empty:
            continue

        model = _make_estimator(params, thread_count=effective_threads)
        model.fit(X_train, y_train)

        preds = model.predict(X_val)
        fold_scores.append(_score_predictions(y_val.to_numpy(), np.asarray(preds), scorer))

    score = float(np.mean(fold_scores)) if fold_scores else -np.inf
    return score


def _save_search_results(
    name: str,
    results_rows: List[Dict[str, Any]],
    best_params: Dict[str, Any] | None,
    best_score: float,
) -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)

    results_path = os.path.join(REPORT_DIR, f"{name}_search_results.csv")
    pd.DataFrame(results_rows).to_csv(results_path, index=False)

    best_path = os.path.join(REPORT_DIR, f"{name}_best_params.json")
    with open(best_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "best_params": best_params,
                "best_score": best_score,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    logger.info("Saved search results to %s", results_path)
    logger.info("Saved best params to %s", best_path)


def _run_search(name, base_params, grid, X, y, scorer):
    X2, y2 = _prepare_X_y(X, y)

    if X2.empty:
        return base_params

    param_grid = list(ParameterGrid(_sanitize_search_grid(grid)))

    print(f"\n{name}: total candidates = {len(param_grid)}")

    logger.info("Stage 1 fast screening started for %s on CPU", name)
    print("\nStage 1: fast screening")

    fast_rows = min(len(X2), HALVING_MAX_ROWS) if HALVING_MAX_ROWS else min(len(X2), 5000)
    X_fast = X2.iloc[-fast_rows:]
    y_fast = y2.iloc[-fast_rows:]

    scores = []
    nested_outer_parallel = is_nested_outer_parallel()
    nested_thread_count = current_worker_thread_count()
    default_workers = max(1, min(4, (os.cpu_count() or 1) - 1))
    workers = int(os.environ.get("CATBOOST_SEARCH_WORKERS", default_workers))
    if nested_outer_parallel:
        workers = 1
        logger.info(
            "Stage 1 fast screening for %s is running inside an outer CPU-parallel worker; forcing worker_count=1 to avoid oversubscription.",
            name,
        )

    def _eval_candidate(idx, params):
        try:
            p = base_params.copy()
            p.update(params)
            orig_iter = int(p.get("iterations", 300))
            p["iterations"] = max(10, int(orig_iter // max(1, HALVING_FACTOR * 2)))
            p["depth"] = min(int(p.get("depth", 8)), 6)

            model = _make_estimator(p, thread_count=nested_thread_count if nested_outer_parallel else None)
            model.fit(X_fast, y_fast)
            preds = model.predict(X_fast)
            score = _score_predictions(y_fast.to_numpy(), np.asarray(preds), scorer)
            return (score, params.copy() if isinstance(params, dict) else params, p.copy(), idx)
        except Exception as exc:
            logger.exception("Stage 1 candidate idx %s failed: %s", idx, exc)
            return None

    progress = tqdm(total=len(param_grid), desc="Stage 1", unit="model")
    recent_intervals = deque(maxlen=50)
    last_ts = None

    futures = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for idx, params in enumerate(param_grid, start=1):
            futures.append(executor.submit(_eval_candidate, idx, params))

        for fut in as_completed(futures):
            now = time.time()
            if last_ts is not None:
                interval = now - last_ts
                if interval <= 0:
                    interval = 1e-6
                recent_intervals.append(interval)
            last_ts = now

            res = fut.result()
            progress.update(1)
            if res:
                scores.append(res)
                best_val = round(max([s for s, *_ in scores]), 6)
                if recent_intervals:
                    rate = len(recent_intervals) / sum(recent_intervals)
                    progress.set_postfix(best=best_val, rps=f"{rate:.2f}model/s")
                else:
                    progress.set_postfix(best=best_val)

    progress.close()
    logger.info("Stage 1 fast screening finished for %s", name)

    if scores:
        best_score_stage1, best_params_stage1, best_p_used_stage1, best_idx_stage1 = max(scores, key=lambda x: x[0])
        print(
            f"\nStage 1 best_score = {best_score_stage1:.6f}, best_params = {best_params_stage1}, "
            f"iterations_used_stage1 = {best_p_used_stage1.get('iterations')}, idx = {best_idx_stage1}"
        )
    else:
        print("\nStage 1 found no successful candidates")

    if ENABLE_GPU_FULL_EVALUATION:
        logger.info("Stage 2 GPU flag is enabled in config for %s, but post-screening evaluation now forces the CPU-parallel path.", name)
    print("\nStage 2: full evaluation")

    top_k = min(4, len(param_grid))
    top_params = sorted(scores, key=lambda x: x[0], reverse=True)[:top_k]

    best_score = -np.inf
    best_params = None

    if HALVING_MAX_ROWS:
        stage2_rows = min(len(X2), max(1000, int(HALVING_MAX_ROWS // 4)))
    else:
        stage2_rows = min(len(X2), 3000)

    X_stage2 = X2.iloc[-stage2_rows:].reset_index(drop=True)
    y_stage2 = y2.iloc[-stage2_rows:].reset_index(drop=True)
    stage2_folds = max(2, int(TIME_SERIES_SPLITS // 2))
    requested_stage2_workers = PARALLEL_EVAL_WORKERS if ENABLE_PARALLEL_CPU_FULL_EVALUATION and not nested_outer_parallel else 1
    stage2_workers, stage2_threads = resolve_parallel_cpu_settings(max(1, len(top_params)), requested_stage2_workers)
    if nested_outer_parallel and nested_thread_count:
        stage2_threads = nested_thread_count
    stage2_parallel = bool(ENABLE_PARALLEL_CPU_FULL_EVALUATION and not nested_outer_parallel and stage2_workers > 1 and len(top_params) > 1)

    logger.info(
        "Stage 2 full evaluation started for %s on CPU-parallel path. enabled=%s workers=%s catboost_thread_count=%s nested_outer_parallel=%s",
        name,
        stage2_parallel,
        stage2_workers,
        stage2_threads,
        nested_outer_parallel,
    )

    stage2_candidates: list[tuple[Dict[str, Any], Dict[str, Any], int]] = []
    for _score_fast, params, _p_used_fast, idx_fast in top_params:
        p = base_params.copy()
        p.update(params)
        orig_iter2 = int(p.get("iterations", base_params.get("iterations", 300)))
        p["iterations"] = max(50, int(orig_iter2 // 2))
        stage2_candidates.append((params, p, idx_fast))

    progress = tqdm(total=len(stage2_candidates), desc="Stage 2", unit="model")
    stage2_completed_in_parallel = False

    if stage2_parallel:
        apply_cpu_worker_limits(stage2_threads)
        try:
            with ProcessPoolExecutor(
                max_workers=stage2_workers,
                mp_context=mp.get_context("spawn"),
                initializer=_initialize_cpu_parallel_worker,
                initargs=(stage2_threads,),
            ) as executor:
                future_to_candidate = {
                    executor.submit(
                        _evaluate_stage2_candidate,
                        p,
                        X_stage2,
                        y_stage2,
                        stage2_folds,
                        search_name=name,
                        scorer=scorer,
                        thread_count=stage2_threads,
                    ): (params, p, idx_fast)
                    for params, p, idx_fast in stage2_candidates
                }

                for future in as_completed(future_to_candidate):
                    params, p, idx_fast = future_to_candidate[future]
                    try:
                        score = future.result()
                        if score > best_score:
                            best_score = score
                            best_params = p
                        progress.update(1)
                        progress.set_postfix(best=round(best_score, 6), path="CPU-parallel")
                    except Exception as exc:
                        logger.exception("Stage 2 candidate evaluation failed for %s idx=%s params=%s: %s", name, idx_fast, params, exc)
                        progress.update(1)
            stage2_completed_in_parallel = True
        except Exception as exc:
            logger.warning(
                "Stage 2 CPU process pool unavailable for %s: %s. Falling back to sequential CPU evaluation.",
                name,
                exc,
            )

    if not stage2_completed_in_parallel:
        if progress.n > 0:
            progress.close()
            progress = tqdm(total=len(stage2_candidates), desc="Stage 2", unit="model")
        apply_cpu_worker_limits(stage2_threads)
        for params, p, idx_fast in stage2_candidates:
            try:
                score = _evaluate_stage2_candidate(
                    p,
                    X_stage2,
                    y_stage2,
                    stage2_folds,
                    search_name=name,
                    scorer=scorer,
                    thread_count=stage2_threads,
                )
                if score > best_score:
                    best_score = score
                    best_params = p
                progress.update(1)
                progress.set_postfix(best=round(best_score, 6), path="CPU")
            except Exception as exc:
                logger.exception("Stage 2 candidate evaluation failed for %s idx=%s params=%s: %s", name, idx_fast, params, exc)
                progress.update(1)

    progress.close()
    logger.info("Stage 2 full evaluation finished for %s on CPU path", name)
    print(f"\n{name}: best_score = {best_score}")
    print(f"{name}: best_params = {best_params}")

    return best_params


def tune_direct_model(X: pd.DataFrame, y: pd.Series) -> Dict[str, Any]:
    return _run_search(
        name="direct",
        base_params=DIRECT_CATBOOST_PARAMS.copy(),
        grid=DIRECT_SEARCH_GRID,
        X=X,
        y=y,
        scorer="neg_mean_absolute_error",
    )


def tune_range_low_model(X: pd.DataFrame, y: pd.Series) -> Dict[str, Any]:
    return _run_search(
        name="range_low",
        base_params=RANGE_LOW_CATBOOST_PARAMS.copy(),
        grid=RANGE_SEARCH_GRID,
        X=X,
        y=y,
        scorer="neg_mean_absolute_error",
    )


def tune_range_high_model(X: pd.DataFrame, y: pd.Series) -> Dict[str, Any]:
    return _run_search(
        name="range_high",
        base_params=RANGE_HIGH_CATBOOST_PARAMS.copy(),
        grid=RANGE_SEARCH_GRID,
        X=X,
        y=y,
        scorer="neg_mean_absolute_error",
    )


tune_range_model_low = tune_range_low_model
tune_range_model_high = tune_range_high_model
