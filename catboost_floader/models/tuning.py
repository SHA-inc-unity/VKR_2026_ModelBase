from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.model_selection import ParameterGrid, TimeSeriesSplit
from tqdm import tqdm

from catboost_floader.core.config import (
    DIRECT_CATBOOST_PARAMS,
    DIRECT_SEARCH_GRID,
    HALVING_MAX_ROWS,
    HALVING_FACTOR,
    RANDOM_SEED,
    RANGE_HIGH_CATBOOST_PARAMS,
    RANGE_LOW_CATBOOST_PARAMS,
    RANGE_SEARCH_GRID,
    REPORT_DIR,
    TIME_SERIES_SPLITS,
    apply_hardware_params,
)
from catboost_floader.core.utils import get_logger

logger = get_logger("hyperparameter_search")


def _make_estimator(params: Dict[str, Any]) -> CatBoostRegressor:
    model_params = dict(params)
    model_params.setdefault("random_seed", RANDOM_SEED)
    model_params.setdefault("verbose", False)
    model_params = apply_hardware_params(model_params)
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

    # Drop non-numeric columns
    drop_cols = []
    for col in X2.columns:
        dtype_str = str(X2[col].dtype)
        if dtype_str in {"object", "string"} or "datetime" in dtype_str:
            drop_cols.append(col)
    if drop_cols:
        X2 = X2.drop(columns=drop_cols, errors="ignore")

    # Replace inf/-inf
    X2 = X2.replace([np.inf, -np.inf], np.nan)

    # Keep only rows valid in both X and y
    valid_mask = X2.notna().all(axis=1) & y2.notna()
    X2 = X2.loc[valid_mask].reset_index(drop=True)
    y2 = y2.loc[valid_mask].reset_index(drop=True)

    # Drop constant columns
    if not X2.empty:
        nunique = X2.nunique(dropna=False)
        constant_cols = nunique[nunique <= 1].index.tolist()
        if constant_cols:
            X2 = X2.drop(columns=constant_cols, errors="ignore")

    # Cap dataset for tuning speed if configured
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

    logger.info(f"Saved search results to {results_path}")
    logger.info(f"Saved best params to {best_path}")


def _run_search(name, base_params, grid, X, y, scorer):
    X2, y2 = _prepare_X_y(X, y)

    if X2.empty:
        return base_params

    param_grid = list(ParameterGrid(_sanitize_search_grid(grid)))

    print(f"\n🔍 {name}: total candidates = {len(param_grid)}")

    # =====================
    # STAGE 1 — FAST SCREEN
    # =====================
    print("\n⚡ Stage 1: fast screening")

    # increase fast screening size (use HALVING_MAX_ROWS cap if configured)
    FAST_ROWS = min(len(X2), HALVING_MAX_ROWS) if HALVING_MAX_ROWS else min(len(X2), 5000)

    X_fast = X2.iloc[-FAST_ROWS:]
    y_fast = y2.iloc[-FAST_ROWS:]

    scores = []

    # Parallel fast screening using ThreadPoolExecutor. We keep worker count conservative.
    default_workers = max(1, min(4, (os.cpu_count() or 1) - 1))
    workers = int(os.environ.get("CATBOOST_SEARCH_WORKERS", default_workers))

    def _eval_candidate(idx, params):
        try:
            p = base_params.copy()
            p.update(params)
            orig_iter = int(p.get("iterations", 300))
            # немного упростим быстрый скрининг: сильнее сокращаем итерации
            # и ограничиваем глубину, чтобы сохранить разнообразие, но снизить время
            p["iterations"] = max(10, int(orig_iter // max(1, HALVING_FACTOR * 2)))
            p["depth"] = min(int(p.get("depth", 8)), 6)

            model = _make_estimator(p)
            model.fit(X_fast, y_fast)
            preds = model.predict(X_fast)
            score = -np.mean(np.abs(y_fast - preds))
            return (score, params.copy() if isinstance(params, dict) else params, p.copy(), idx)
        except Exception as e:
            logger.exception(f"Stage1 candidate idx {idx} failed: {e}")
            return None

    progress = tqdm(total=len(param_grid), desc="Stage 1", unit="model")

    # rolling intervals for rate calculation
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
    # after Stage 1, report the best candidate found on the fast subset
    if scores:
        best_score_stage1, best_params_stage1, best_p_used_stage1, best_idx_stage1 = max(scores, key=lambda x: x[0])
        print(
            f"\n➡️ Stage 1 best_score = {best_score_stage1:.6f}, best_params = {best_params_stage1}, iterations_used_stage1 = {best_p_used_stage1.get('iterations')}, idx = {best_idx_stage1}"
        )
    else:
        print("\n➡️ Stage 1 found no successful candidates")

    # =====================
    # STAGE 2 — TOP K
    # =====================
    print("\n🔥 Stage 2: full evaluation")

    # =====================
    # STAGE 2 — FULL EVALUATION (упрощённая и быстрая)
    # =====================
    # сильно упрощаем полный этап: берём небольшое число лучших кандидатов,
    # уменьшаем число фолдов и используем меньшую подвыборку данных и итераций
    TOP_K = min(4, len(param_grid))
    top_params = sorted(scores, key=lambda x: x[0], reverse=True)[:TOP_K]

    best_score = -np.inf
    best_params = None

    # use smaller dataset for Stage 2 to speed up evaluation (fallbacks included)
    if HALVING_MAX_ROWS:
        STAGE2_ROWS = min(len(X2), max(1000, int(HALVING_MAX_ROWS // 4)))
    else:
        STAGE2_ROWS = min(len(X2), 3000)

    X_stage2 = X2.iloc[-STAGE2_ROWS:].reset_index(drop=True)
    y_stage2 = y2.iloc[-STAGE2_ROWS:].reset_index(drop=True)

    # reduce folds (e.g., 4 -> 2) for much faster evaluation
    STAGE2_FOLDS = max(2, int(TIME_SERIES_SPLITS // 2))

    progress = tqdm(top_params, desc="Stage 2", unit="model")

    for score_fast, params, p_used_fast, idx_fast in progress:
        try:
            p = base_params.copy()
            p.update(params)

            # reduce iterations for Stage2 (to speed up full evaluation)
            orig_iter2 = int(p.get("iterations", base_params.get("iterations", 300)))
            p["iterations"] = max(50, int(orig_iter2 // 2))

            model = _make_estimator(p)

            tscv = TimeSeriesSplit(n_splits=STAGE2_FOLDS)

            fold_scores = []

            for train_idx, val_idx in tscv.split(X_stage2):
                X_train = X_stage2.iloc[train_idx]
                y_train = y_stage2.iloc[train_idx]

                X_val = X_stage2.iloc[val_idx]
                y_val = y_stage2.iloc[val_idx]

                model.fit(X_train, y_train)

                preds = model.predict(X_val)

                fold_scores.append(-np.mean(np.abs(y_val - preds)))

            score = np.mean(fold_scores) if fold_scores else -np.inf

            if score > best_score:
                best_score = score
                best_params = p

            progress.set_postfix(best=round(best_score, 6))

        except Exception:
            continue

    print(f"\n✅ {name}: best_score = {best_score}")
    print(f"✅ {name}: best_params = {best_params}")

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


# backward-compatible aliases
tune_range_model_low = tune_range_low_model
tune_range_model_high = tune_range_high_model