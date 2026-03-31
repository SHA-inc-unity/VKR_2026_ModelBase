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
    format_cpu_stage_policy_log,
    HALVING_FACTOR,
    HALVING_MAX_ROWS,
    is_nested_outer_parallel,
    RANDOM_SEED,
    RANGE_HIGH_CATBOOST_PARAMS,
    RANGE_LOW_CATBOOST_PARAMS,
    RANGE_SEARCH_GRID,
    REPORT_DIR,
    resolve_cpu_stage_parallel_policy,
    resolve_stage2_parallel_policy,
    TIME_SERIES_SPLITS,
)
from catboost_floader.core.utils import get_logger

logger = get_logger("hyperparameter_search")
_STAGE2_WORKER_STATE: Dict[str, Any] = {}


def _initialize_cpu_parallel_worker(
    thread_count: int,
    X_stage2: pd.DataFrame | None = None,
    y_stage2: pd.Series | None = None,
    fold_specs: List[Tuple[np.ndarray, np.ndarray]] | None = None,
) -> None:
    apply_cpu_worker_limits(thread_count)
    if X_stage2 is not None and y_stage2 is not None and fold_specs is not None:
        global _STAGE2_WORKER_STATE
        _STAGE2_WORKER_STATE = {
            "X_stage2": X_stage2,
            "y_stage2": y_stage2,
            "fold_specs": fold_specs,
        }


def _format_duration(seconds: float) -> str:
    safe_seconds = max(0.0, float(seconds))
    whole_seconds = int(safe_seconds)
    milliseconds = int(round((safe_seconds - whole_seconds) * 1000.0))
    if milliseconds == 1000:
        whole_seconds += 1
        milliseconds = 0
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def _log_stage2_duration(name: str, elapsed_seconds: float) -> None:
    logger.info("Stage 2 full evaluation total time for %s: %.2f seconds", name, elapsed_seconds)
    logger.info("Stage 2 full evaluation total time for %s: %s", name, _format_duration(elapsed_seconds))


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


def _build_stage2_fold_specs(X_stage2: pd.DataFrame, folds: int) -> List[Tuple[np.ndarray, np.ndarray]]:
    tscv = TimeSeriesSplit(n_splits=folds)
    fold_specs: List[Tuple[np.ndarray, np.ndarray]] = []
    for train_idx, val_idx in tscv.split(X_stage2):
        if len(train_idx) == 0 or len(val_idx) == 0:
            continue
        fold_specs.append(
            (
                np.asarray(train_idx, dtype=np.int64),
                np.asarray(val_idx, dtype=np.int64),
            )
        )
    return fold_specs


def _resolve_stage2_inputs(
    X_stage2: pd.DataFrame | None,
    y_stage2: pd.Series | None,
    fold_specs: List[Tuple[np.ndarray, np.ndarray]] | None,
) -> tuple[pd.DataFrame, pd.Series, List[Tuple[np.ndarray, np.ndarray]]]:
    if X_stage2 is not None and y_stage2 is not None and fold_specs is not None:
        return X_stage2, y_stage2, fold_specs
    if not _STAGE2_WORKER_STATE:
        raise RuntimeError("Stage 2 worker state is not initialized")
    return (
        _STAGE2_WORKER_STATE["X_stage2"],
        _STAGE2_WORKER_STATE["y_stage2"],
        _STAGE2_WORKER_STATE["fold_specs"],
    )


def _score_stage2_fold(
    params: Dict[str, Any],
    fold_idx: int,
    *,
    scorer: str,
    thread_count: int | None = None,
    X_stage2: pd.DataFrame | None = None,
    y_stage2: pd.Series | None = None,
    fold_specs: List[Tuple[np.ndarray, np.ndarray]] | None = None,
) -> float:
    X_data, y_data, fold_data = _resolve_stage2_inputs(X_stage2, y_stage2, fold_specs)
    train_idx, val_idx = fold_data[fold_idx]
    X_train = X_data.iloc[train_idx]
    y_train = y_data.iloc[train_idx]
    X_val = X_data.iloc[val_idx]
    y_val = y_data.iloc[val_idx]
    effective_threads = thread_count or current_worker_thread_count()

    model = _make_estimator(params, thread_count=effective_threads)
    model.fit(X_train, y_train)
    preds = model.predict(X_val)
    return _score_predictions(y_val.to_numpy(), np.asarray(preds), scorer)


def _evaluate_stage2_fold_job(
    candidate_idx: int,
    params: Dict[str, Any],
    fold_idx: int,
    *,
    scorer: str,
    thread_count: int | None = None,
) -> tuple[int, int, float]:
    score = _score_stage2_fold(
        params,
        fold_idx,
        scorer=scorer,
        thread_count=thread_count,
    )
    return candidate_idx, fold_idx, score


def _evaluate_stage2_candidate_job(
    candidate_idx: int,
    params: Dict[str, Any],
    *,
    scorer: str,
    thread_count: int | None = None,
) -> tuple[int, float]:
    fold_specs = _STAGE2_WORKER_STATE.get("fold_specs", [])
    if not fold_specs:
        raise RuntimeError("Stage 2 fold specs are not initialized")
    fold_scores = [
        _score_stage2_fold(
            params,
            fold_idx,
            scorer=scorer,
            thread_count=thread_count,
        )
        for fold_idx in range(len(fold_specs))
    ]
    return candidate_idx, float(np.mean(fold_scores)) if fold_scores else -np.inf


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
    fold_specs = _build_stage2_fold_specs(X_stage2, folds)
    if not fold_specs:
        return -np.inf
    fold_scores = [
        _score_stage2_fold(
            params,
            fold_idx,
            scorer=scorer,
            thread_count=thread_count,
            X_stage2=X_stage2,
            y_stage2=y_stage2,
            fold_specs=fold_specs,
        )
        for fold_idx in range(len(fold_specs))
    ]
    return float(np.mean(fold_scores)) if fold_scores else -np.inf


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


def _run_stage2_full_evaluation(
    *,
    name: str,
    base_params: Dict[str, Any],
    top_params: List[Tuple[float, Dict[str, Any], Dict[str, Any], int]],
    X_stage2: pd.DataFrame,
    y_stage2: pd.Series,
    folds: int,
    scorer: str,
    nested_outer_parallel: bool,
) -> tuple[float, Dict[str, Any] | None]:
    fold_specs = _build_stage2_fold_specs(X_stage2, folds)
    stage2_candidates: List[Dict[str, Any]] = []
    for candidate_idx, (_score_fast, params, _p_used_fast, idx_fast) in enumerate(top_params):
        candidate_params = base_params.copy()
        candidate_params.update(params)
        orig_iter = int(candidate_params.get("iterations", base_params.get("iterations", 300)))
        candidate_params["iterations"] = max(50, int(orig_iter // 2))
        stage2_candidates.append(
            {
                "candidate_idx": candidate_idx,
                "search_idx": idx_fast,
                "stage1_params": dict(params),
                "params": candidate_params,
            }
        )

    stage2_policy = resolve_stage2_parallel_policy(
        len(stage2_candidates),
        len(fold_specs),
        nested_outer_parallel=nested_outer_parallel,
    )

    stage2_started_at = time.perf_counter()
    logger.info(
        "Stage 2 full evaluation using CPU policy for %s: %s",
        name,
        format_cpu_stage_policy_log(stage2_policy),
    )

    if not stage2_candidates or not fold_specs:
        elapsed = time.perf_counter() - stage2_started_at
        logger.info("Stage 2 full evaluation finished for %s on CPU path", name)
        _log_stage2_duration(name, elapsed)
        return -np.inf, None

    progress_total = len(stage2_candidates) * len(fold_specs) if stage2_policy["granularity"] == "candidate_fold" else len(stage2_candidates)
    progress = tqdm(total=progress_total, desc=f"Stage 2 ({stage2_policy['granularity']})", unit="job")

    candidate_lookup = {candidate["candidate_idx"]: candidate for candidate in stage2_candidates}
    candidate_scores: Dict[int, float] = {}
    candidate_fold_scores: Dict[int, List[float]] = {candidate["candidate_idx"]: [] for candidate in stage2_candidates}
    failed_candidates: set[int] = set()
    execution_path = "sequential_cpu"

    def _reset_stage2_results() -> None:
        candidate_scores.clear()
        candidate_fold_scores.clear()
        for candidate in stage2_candidates:
            candidate_fold_scores[candidate["candidate_idx"]] = []
        failed_candidates.clear()

    def _set_progress_postfix() -> None:
        if stage2_policy["granularity"] == "candidate_fold":
            completed_scores = {
                idx: float(np.mean(scores))
                for idx, scores in candidate_fold_scores.items()
                if idx not in failed_candidates and len(scores) == len(fold_specs)
            }
            best_display = round(max(completed_scores.values()), 6) if completed_scores else "warming"
            progress.set_postfix(best=best_display, completed=f"{len(completed_scores)}/{len(stage2_candidates)}")
        else:
            best_display = round(max(candidate_scores.values()), 6) if candidate_scores else "warming"
            progress.set_postfix(best=best_display, completed=f"{len(candidate_scores)}/{len(stage2_candidates)}")

    def _finalize_candidate_scores() -> None:
        if stage2_policy["granularity"] != "candidate_fold":
            return
        for candidate in stage2_candidates:
            candidate_idx = candidate["candidate_idx"]
            fold_scores = candidate_fold_scores.get(candidate_idx, [])
            if candidate_idx in failed_candidates or len(fold_scores) != len(fold_specs):
                continue
            candidate_scores[candidate_idx] = float(np.mean(fold_scores))

    def _run_parallel_with_executor(executor_kind: str) -> None:
        nonlocal execution_path
        if executor_kind == "process":
            executor_cm = ProcessPoolExecutor(
                max_workers=stage2_policy["outer_workers"],
                mp_context=mp.get_context("spawn"),
                initializer=_initialize_cpu_parallel_worker,
                initargs=(stage2_policy["inner_threads"], X_stage2, y_stage2, fold_specs),
            )
        elif executor_kind == "thread":
            _initialize_cpu_parallel_worker(stage2_policy["inner_threads"], X_stage2, y_stage2, fold_specs)
            executor_cm = ThreadPoolExecutor(max_workers=stage2_policy["outer_workers"])
        else:
            raise ValueError(f"Unsupported executor kind: {executor_kind}")

        with executor_cm as executor:
            if stage2_policy["granularity"] == "candidate_fold":
                future_to_job = {
                    executor.submit(
                        _evaluate_stage2_fold_job,
                        candidate["candidate_idx"],
                        candidate["params"],
                        fold_idx,
                        scorer=scorer,
                        thread_count=stage2_policy["inner_threads"],
                    ): (candidate["candidate_idx"], fold_idx)
                    for candidate in stage2_candidates
                    for fold_idx in range(len(fold_specs))
                }
                for future in as_completed(future_to_job):
                    candidate_idx, fold_idx = future_to_job[future]
                    try:
                        result_candidate_idx, _result_fold_idx, score = future.result()
                        candidate_fold_scores[result_candidate_idx].append(score)
                    except Exception as exc:
                        failed_candidates.add(candidate_idx)
                        candidate = candidate_lookup[candidate_idx]
                        logger.exception(
                            "Stage 2 fold evaluation failed for %s candidate_idx=%s fold_idx=%s params=%s: %s",
                            name,
                            candidate["search_idx"],
                            fold_idx + 1,
                            candidate["stage1_params"],
                            exc,
                        )
                    progress.update(1)
                    _set_progress_postfix()
            else:
                future_to_job = {
                    executor.submit(
                        _evaluate_stage2_candidate_job,
                        candidate["candidate_idx"],
                        candidate["params"],
                        scorer=scorer,
                        thread_count=stage2_policy["inner_threads"],
                    ): candidate["candidate_idx"]
                    for candidate in stage2_candidates
                }
                for future in as_completed(future_to_job):
                    candidate_idx = future_to_job[future]
                    try:
                        result_candidate_idx, score = future.result()
                        candidate_scores[result_candidate_idx] = score
                    except Exception as exc:
                        failed_candidates.add(candidate_idx)
                        candidate = candidate_lookup[candidate_idx]
                        logger.exception(
                            "Stage 2 candidate evaluation failed for %s candidate_idx=%s params=%s: %s",
                            name,
                            candidate["search_idx"],
                            candidate["stage1_params"],
                            exc,
                        )
                    progress.update(1)
                    _set_progress_postfix()

        execution_path = f"{executor_kind}_{stage2_policy['granularity']}"

    def _run_sequential() -> None:
        nonlocal execution_path
        apply_cpu_worker_limits(stage2_policy["inner_threads"])
        if stage2_policy["granularity"] == "candidate_fold":
            for candidate in stage2_candidates:
                for fold_idx in range(len(fold_specs)):
                    try:
                        score = _score_stage2_fold(
                            candidate["params"],
                            fold_idx,
                            scorer=scorer,
                            thread_count=stage2_policy["inner_threads"],
                            X_stage2=X_stage2,
                            y_stage2=y_stage2,
                            fold_specs=fold_specs,
                        )
                        candidate_fold_scores[candidate["candidate_idx"]].append(score)
                    except Exception as exc:
                        failed_candidates.add(candidate["candidate_idx"])
                        logger.exception(
                            "Stage 2 fold evaluation failed for %s candidate_idx=%s fold_idx=%s params=%s: %s",
                            name,
                            candidate["search_idx"],
                            fold_idx + 1,
                            candidate["stage1_params"],
                            exc,
                        )
                    progress.update(1)
                    _set_progress_postfix()
        else:
            for candidate in stage2_candidates:
                try:
                    score = _evaluate_stage2_candidate(
                        candidate["params"],
                        X_stage2,
                        y_stage2,
                        len(fold_specs),
                        search_name=name,
                        scorer=scorer,
                        thread_count=stage2_policy["inner_threads"],
                    )
                    candidate_scores[candidate["candidate_idx"]] = score
                except Exception as exc:
                    failed_candidates.add(candidate["candidate_idx"])
                    logger.exception(
                        "Stage 2 candidate evaluation failed for %s candidate_idx=%s params=%s: %s",
                        name,
                        candidate["search_idx"],
                        candidate["stage1_params"],
                        exc,
                    )
                progress.update(1)
                _set_progress_postfix()
        execution_path = f"sequential_{stage2_policy['granularity']}"

    parallel_completed = False
    if stage2_policy["parallel_enabled"]:
        try:
            _run_parallel_with_executor("process")
            parallel_completed = True
        except Exception as exc:
            logger.warning(
                "Stage 2 CPU process pool unavailable for %s: %s. Falling back to thread-based CPU evaluation.",
                name,
                exc,
            )
            _reset_stage2_results()
            if progress.n > 0:
                progress.close()
                progress = tqdm(total=progress_total, desc=f"Stage 2 ({stage2_policy['granularity']})", unit="job")
            try:
                _run_parallel_with_executor("thread")
                parallel_completed = True
            except Exception as thread_exc:
                logger.warning(
                    "Stage 2 CPU thread pool unavailable for %s: %s. Falling back to sequential CPU evaluation.",
                    name,
                    thread_exc,
                )
                _reset_stage2_results()
                if progress.n > 0:
                    progress.close()
                    progress = tqdm(total=progress_total, desc=f"Stage 2 ({stage2_policy['granularity']})", unit="job")

    if not parallel_completed:
        _run_sequential()

    _finalize_candidate_scores()
    progress.close()

    best_score = -np.inf
    best_params = None
    for candidate in stage2_candidates:
        candidate_idx = candidate["candidate_idx"]
        score = candidate_scores.get(candidate_idx, -np.inf)
        if score > best_score:
            best_score = score
            best_params = candidate["params"]

    elapsed = time.perf_counter() - stage2_started_at
    logger.info("Stage 2 full evaluation finished for %s on CPU path using %s", name, execution_path)
    _log_stage2_duration(name, elapsed)
    return best_score, best_params


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
    stage1_policy = resolve_cpu_stage_parallel_policy(
        "fast_screening",
        parallel_units=len(param_grid),
        granularity="candidate",
        nested_outer_parallel=nested_outer_parallel,
        nested_thread_count=nested_thread_count if nested_outer_parallel else None,
        allow_parallel=ENABLE_PARALLEL_CPU_FULL_EVALUATION,
    )
    workers = int(stage1_policy["outer_workers"])
    stage1_inner_threads = int(stage1_policy["inner_threads"])
    apply_cpu_worker_limits(stage1_inner_threads, mark_outer_parallel=nested_outer_parallel)
    logger.info(
        "Stage 1 fast screening using CPU policy for %s: %s",
        name,
        format_cpu_stage_policy_log(stage1_policy),
    )

    def _eval_candidate(idx, params):
        try:
            p = base_params.copy()
            p.update(params)
            orig_iter = int(p.get("iterations", 300))
            p["iterations"] = max(10, int(orig_iter // max(1, HALVING_FACTOR * 2)))
            p["depth"] = min(int(p.get("depth", 8)), 6)

            model = _make_estimator(p, thread_count=stage1_inner_threads)
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
    best_score, best_params = _run_stage2_full_evaluation(
        name=name,
        base_params=base_params,
        top_params=top_params,
        X_stage2=X_stage2,
        y_stage2=y_stage2,
        folds=stage2_folds,
        scorer=scorer,
        nested_outer_parallel=nested_outer_parallel,
    )
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
