from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Any, Dict

import numpy as np
import pandas as pd

from catboost_floader.core.config import DIRECTION_DEADBAND
from catboost_floader.core.parallel_policy import (
    apply_cpu_worker_limits,
    current_worker_thread_count,
    ENABLE_PARALLEL_CPU_BACKTEST_WINDOW,
    format_cpu_stage_policy_log,
    is_nested_outer_parallel,
    resolve_cpu_stage_parallel_policy,
)
from catboost_floader.core.utils import ensure_dirs, get_logger, save_json

logger = get_logger("multi_window_eval")
_WINDOW_EVAL_STATE: dict[str, Any] = {}


def _accuracy_pct(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        value_f = float(value)
    except Exception:
        return None
    if np.isnan(value_f):
        return None
    return round(value_f * 100.0, 2)


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2:
        return float("nan")
    if np.allclose(np.std(a), 0.0) or np.allclose(np.std(b), 0.0):
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _label_from_return(values: np.ndarray, deadband: float) -> np.ndarray:
    labels = np.zeros(len(values), dtype=int)
    labels[values > deadband] = 1
    labels[values < -deadband] = -1
    return labels


def _nanmean(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() == 0:
        return None
    return float(numeric.mean())


def _nanstd(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() == 0:
        return None
    return float(numeric.std(ddof=0))


def _resolve_window_specs(
    total_rows: int,
    window_count: int,
    window_size: int,
    window_step: int,
) -> list[dict[str, int]]:
    total = max(0, int(total_rows or 0))
    if total == 0:
        return []

    count = max(1, int(window_count or 1))
    if total >= 2:
        # Keep at least 2 rows per window when possible.
        count = min(count, max(1, total // 2))
    size_cfg = int(window_size or 0)
    step_cfg = int(window_step or 0)

    # Auto mode (size=0, step=0): split into contiguous chronological windows.
    if size_cfg <= 0 and step_cfg <= 0 and count > 1:
        edges = np.linspace(0, total, num=count + 1, dtype=int)
        contiguous_specs: list[dict[str, int]] = []
        for idx in range(count):
            start = int(edges[idx])
            end = int(edges[idx + 1])
            if end <= start:
                end = min(total, start + 1)
            if total >= 2 and end - start < 2:
                end = min(total, start + 2)
            if end <= start:
                continue
            contiguous_specs.append(
                {
                    "window_index": len(contiguous_specs) + 1,
                    "start_row": start,
                    "end_row": end,
                }
            )
        if contiguous_specs:
            return contiguous_specs

    if size_cfg <= 0:
        if count <= 1:
            size = total
        else:
            size = max(2, total // count)
    else:
        size = max(2, min(size_cfg, total))

    if step_cfg <= 0:
        if count <= 1:
            step = size
        else:
            span = max(0, total - size)
            step = max(1, span // max(1, count - 1))
    else:
        step = max(1, step_cfg)

    max_start = max(0, total - size)
    starts: list[int] = []
    current = 0
    while current <= max_start:
        starts.append(int(current))
        current += step
    if not starts:
        starts = [0]
    if starts[-1] != max_start:
        starts.append(max_start)

    if len(starts) > count:
        sampled = np.linspace(0, len(starts) - 1, num=count, dtype=int)
        starts = [starts[int(i)] for i in sampled]

    uniq_starts: list[int] = []
    seen = set()
    for start in starts:
        if start in seen:
            continue
        seen.add(start)
        uniq_starts.append(start)

    specs: list[dict[str, int]] = []
    for idx, start in enumerate(uniq_starts, start=1):
        end = min(total, start + size)
        if total >= 2 and end - start < 2:
            start = max(0, end - 2)
            end = min(total, start + 2)
        specs.append(
            {
                "window_index": idx,
                "start_row": int(start),
                "end_row": int(end),
            }
        )

    return specs


def _compute_window_metrics(window_df: pd.DataFrame, deadband: float) -> dict[str, Any] | None:
    if window_df.empty:
        return None

    y_true_price = pd.to_numeric(window_df.get("target_future_close"), errors="coerce")
    y_pred_price = pd.to_numeric(window_df.get("direct_pred_price"), errors="coerce")
    y_base_price = pd.to_numeric(window_df.get("baseline_persistence_price"), errors="coerce")
    y_true_ret = pd.to_numeric(window_df.get("target_return"), errors="coerce")
    y_pred_ret = pd.to_numeric(window_df.get("direct_pred_return"), errors="coerce")

    price_mask = y_true_price.notna() & y_pred_price.notna()
    ret_mask = y_true_ret.notna() & y_pred_ret.notna()
    base_mask = y_true_price.notna() & y_base_price.notna()

    if int(price_mask.sum()) == 0 or int(ret_mask.sum()) == 0:
        return None

    y_true_price_np = y_true_price.loc[price_mask].to_numpy(dtype=float)
    y_pred_price_np = y_pred_price.loc[price_mask].to_numpy(dtype=float)
    y_true_ret_np = y_true_ret.loc[ret_mask].to_numpy(dtype=float)
    y_pred_ret_np = y_pred_ret.loc[ret_mask].to_numpy(dtype=float)

    mae = float(np.mean(np.abs(y_true_price_np - y_pred_price_np)))
    rmse = float(np.sqrt(np.mean((y_true_price_np - y_pred_price_np) ** 2)))
    mape = float(np.mean(np.abs((y_true_price_np - y_pred_price_np) / (np.abs(y_true_price_np) + 1e-8))) * 100.0)
    sign_acc = float(np.mean(np.sign(y_true_ret_np) == np.sign(y_pred_ret_np)))
    corr = _safe_corr(y_true_ret_np, y_pred_ret_np)

    baseline_mae = None
    delta_vs_baseline = None
    if int(base_mask.sum()) > 0:
        y_true_base_np = y_true_price.loc[base_mask].to_numpy(dtype=float)
        y_base_price_np = y_base_price.loc[base_mask].to_numpy(dtype=float)
        baseline_mae = float(np.mean(np.abs(y_true_base_np - y_base_price_np)))
        delta_vs_baseline = float(baseline_mae - mae)

    direction_label_accuracy = None
    direction_accuracy_pct = None
    if "direction_pred_label" in window_df.columns:
        direction_pred = pd.to_numeric(window_df["direction_pred_label"], errors="coerce")
        direction_mask = ret_mask & direction_pred.notna()
        if int(direction_mask.sum()) > 0:
            y_true_ret_dir = y_true_ret.loc[direction_mask].to_numpy(dtype=float)
            y_pred_lbl = direction_pred.loc[direction_mask].to_numpy(dtype=int)
            y_true_lbl = _label_from_return(y_true_ret_dir, deadband)
            direction_label_accuracy = float(np.mean(y_pred_lbl == y_true_lbl))
            direction_accuracy_pct = _accuracy_pct(direction_label_accuracy)

    return {
        "MAE": mae,
        "RMSE": rmse,
        "MAPE": mape,
        "sign_accuracy": sign_acc,
        "sign_accuracy_pct": _accuracy_pct(sign_acc),
        "correlation": corr,
        "baseline_MAE": baseline_mae,
        "delta_vs_baseline": delta_vs_baseline,
        "direction_label_accuracy": direction_label_accuracy,
        "direction_accuracy_pct": direction_accuracy_pct,
        "window_rows": int(len(window_df)),
        "window_price_points": int(price_mask.sum()),
        "window_return_points": int(ret_mask.sum()),
    }


def _aggregate_window_metrics(window_metrics_df: pd.DataFrame, model_key: str) -> dict[str, Any]:
    if window_metrics_df.empty:
        return {
            "model_key": model_key,
            "windows_evaluated": 0,
            "window_count": 0,
            "mean_MAE": None,
            "std_MAE": None,
            "mean_delta_vs_baseline": None,
            "std_delta_vs_baseline": None,
            "mean_sign_accuracy_pct": None,
            "std_sign_accuracy_pct": None,
            "mean_direction_accuracy_pct": None,
            "model_win_rate_vs_baseline": None,
            "win_rate_vs_baseline": None,
            "worst_window_delta_vs_baseline": None,
            "best_window_delta_vs_baseline": None,
        }

    delta_col = pd.to_numeric(window_metrics_df["delta_vs_baseline"], errors="coerce")
    delta_valid = delta_col.dropna()
    win_rate = None
    worst_delta = None
    best_delta = None
    if not delta_valid.empty:
        win_rate = float((delta_valid > 0.0).mean())
        worst_delta = float(delta_valid.min())
        best_delta = float(delta_valid.max())

    direction_pct = pd.to_numeric(window_metrics_df["direction_accuracy_pct"], errors="coerce")

    return {
        "model_key": model_key,
        "windows_evaluated": int(len(window_metrics_df)),
        "window_count": int(len(window_metrics_df)),
        "mean_MAE": _nanmean(window_metrics_df["MAE"]),
        "std_MAE": _nanstd(window_metrics_df["MAE"]),
        "mean_delta_vs_baseline": _nanmean(window_metrics_df["delta_vs_baseline"]),
        "std_delta_vs_baseline": _nanstd(window_metrics_df["delta_vs_baseline"]),
        "mean_sign_accuracy_pct": _nanmean(window_metrics_df["sign_accuracy_pct"]),
        "std_sign_accuracy_pct": _nanstd(window_metrics_df["sign_accuracy_pct"]),
        "mean_direction_accuracy_pct": None if direction_pct.notna().sum() == 0 else float(direction_pct.mean()),
        "model_win_rate_vs_baseline": win_rate,
        "win_rate_vs_baseline": win_rate,
        "worst_window_delta_vs_baseline": worst_delta,
        "best_window_delta_vs_baseline": best_delta,
    }


def _initialize_window_eval_worker(
    thread_count: int | None,
    work_df: pd.DataFrame,
    deadband: float,
    mark_outer_parallel: bool = False,
    execution_mode: str | None = None,
) -> None:
    apply_cpu_worker_limits(
        thread_count,
        mark_outer_parallel=mark_outer_parallel,
        execution_mode=execution_mode,
    )
    global _WINDOW_EVAL_STATE
    _WINDOW_EVAL_STATE = {
        "work_df": work_df,
        "deadband": float(deadband),
    }


def _evaluate_single_window_job(
    spec: dict[str, int],
    work_df: pd.DataFrame | None = None,
    deadband: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    source_df = work_df if work_df is not None else _WINDOW_EVAL_STATE.get("work_df")
    resolved_deadband = deadband if deadband is not None else _WINDOW_EVAL_STATE.get("deadband")
    if source_df is None or resolved_deadband is None:
        raise RuntimeError("Window evaluation worker state is not initialized")

    start = int(spec["start_row"])
    end = int(spec["end_row"])
    if end <= start:
        return None

    window_df = source_df.iloc[start:end].copy()
    metrics = _compute_window_metrics(window_df, float(resolved_deadband))
    if metrics is None:
        return None

    start_ts = None
    end_ts = None
    if "timestamp" in window_df.columns and len(window_df) > 0:
        start_raw = window_df["timestamp"].iloc[0]
        end_raw = window_df["timestamp"].iloc[-1]
        start_ts = None if pd.isna(start_raw) else pd.Timestamp(start_raw).isoformat()
        end_ts = None if pd.isna(end_raw) else pd.Timestamp(end_raw).isoformat()

    row = {
        "window_index": int(spec["window_index"]),
        "window_start_row": start,
        "window_end_row": end - 1,
        "window_start_timestamp": start_ts,
        "window_end_timestamp": end_ts,
    }
    row.update(metrics)
    window_meta = {
        "window_index": int(spec["window_index"]),
        "start_row": start,
        "end_row_exclusive": end,
        "window_rows": int(len(window_df)),
        "start_timestamp": start_ts,
        "end_timestamp": end_ts,
    }
    return row, window_meta


def _evaluate_model_multi_window_core(
    backtest_df: pd.DataFrame,
    *,
    model_key: str,
    window_count: int,
    window_size: int,
    window_step: int,
    deadband: float,
) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    if backtest_df is None or backtest_df.empty:
        empty_df = pd.DataFrame(
            columns=[
                "window_index",
                "window_start_row",
                "window_end_row",
                "window_start_timestamp",
                "window_end_timestamp",
                "window_rows",
                "window_price_points",
                "window_return_points",
                "MAE",
                "RMSE",
                "MAPE",
                "sign_accuracy",
                "sign_accuracy_pct",
                "correlation",
                "baseline_MAE",
                "delta_vs_baseline",
                "direction_label_accuracy",
                "direction_accuracy_pct",
            ]
        )
        return empty_df, [], _aggregate_window_metrics(empty_df, model_key)

    work_df = backtest_df.copy().reset_index(drop=True)
    if "timestamp" in work_df.columns:
        work_df["timestamp"] = pd.to_datetime(work_df["timestamp"], utc=True, errors="coerce")

    window_specs = _resolve_window_specs(len(work_df), window_count, window_size, window_step)
    nested_outer_parallel = is_nested_outer_parallel()
    nested_thread_count = current_worker_thread_count()
    window_policy = resolve_cpu_stage_parallel_policy(
        "backtest_window_evaluation",
        parallel_units=max(1, len(window_specs)),
        granularity="backtest_window",
        nested_outer_parallel=nested_outer_parallel,
        nested_thread_count=nested_thread_count if nested_outer_parallel else None,
        allow_parallel=ENABLE_PARALLEL_CPU_BACKTEST_WINDOW,
    )
    window_inner_threads = window_policy.get("catboost_thread_count")
    apply_cpu_worker_limits(
        window_inner_threads,
        mark_outer_parallel=nested_outer_parallel,
        execution_mode=window_policy.get("execution_mode"),
    )
    logger.info(
        "Backtest window evaluation using CPU policy for model=%s: %s",
        model_key,
        format_cpu_stage_policy_log(window_policy),
    )

    rows: list[dict[str, Any]] = []
    windows_meta: list[dict[str, Any]] = []

    def _append_window_result(spec: dict[str, int], result) -> None:
        if result is None:
            return
        row, window_meta = result
        rows.append(row)
        windows_meta.append(window_meta)

    def _run_window_parallel(executor_kind: str) -> None:
        if executor_kind == "process":
            executor_cm = ProcessPoolExecutor(
                max_workers=int(window_policy["outer_workers"]),
                mp_context=mp.get_context("spawn"),
                initializer=_initialize_window_eval_worker,
                initargs=(
                    window_inner_threads,
                    work_df,
                    deadband,
                    True,
                    window_policy.get("execution_mode"),
                ),
            )
        elif executor_kind == "thread":
            _initialize_window_eval_worker(
                window_inner_threads,
                work_df,
                deadband,
                nested_outer_parallel,
                window_policy.get("execution_mode"),
            )
            executor_cm = ThreadPoolExecutor(max_workers=int(window_policy["outer_workers"]))
        else:
            raise ValueError(f"Unsupported executor kind: {executor_kind}")

        with executor_cm as executor:
            future_to_spec = {
                executor.submit(_evaluate_single_window_job, spec): spec for spec in window_specs
                for spec in window_specs
            }
            for future in as_completed(future_to_spec):
                spec = future_to_spec[future]
                try:
                    _append_window_result(spec, future.result())
                except Exception as exc:
                    logger.exception(
                        "Multi-window evaluation failed for model=%s window_index=%s: %s",
                        model_key,
                        spec.get("window_index"),
                        exc,
                    )

    def _run_window_sequential() -> None:
        _initialize_window_eval_worker(
            window_inner_threads,
            work_df,
            deadband,
            nested_outer_parallel,
            window_policy.get("execution_mode"),
        )
        for spec in window_specs:
            try:
                _append_window_result(
                    spec,
                    _evaluate_single_window_job(spec, work_df=work_df, deadband=deadband),
                )
            except Exception as exc:
                logger.exception(
                    "Multi-window evaluation failed for model=%s window_index=%s: %s",
                    model_key,
                    spec.get("window_index"),
                    exc,
                )

    if window_policy["parallel_enabled"] and len(window_specs) > 1:
        try:
            _run_window_parallel("process")
        except Exception as exc:
            logger.warning(
                "Multi-window CPU process pool unavailable for model=%s: %s. Falling back to thread-based evaluation.",
                model_key,
                exc,
            )
            rows.clear()
            windows_meta.clear()
            try:
                _run_window_parallel("thread")
            except Exception as thread_exc:
                logger.warning(
                    "Multi-window CPU thread pool unavailable for model=%s: %s. Falling back to sequential evaluation.",
                    model_key,
                    thread_exc,
                )
                rows.clear()
                windows_meta.clear()
                _run_window_sequential()
    else:
        _run_window_sequential()

    if rows:
        rows = sorted(rows, key=lambda item: int(item.get("window_index", 0)))
    if windows_meta:
        windows_meta = sorted(windows_meta, key=lambda item: int(item.get("window_index", 0)))

    metrics_df = pd.DataFrame(rows)
    aggregate_metrics = _aggregate_window_metrics(metrics_df, model_key)
    return metrics_df, windows_meta, aggregate_metrics


def evaluate_model_multi_window_in_memory(
    backtest_df: pd.DataFrame,
    *,
    model_key: str,
    window_count: int,
    window_size: int,
    window_step: int,
    deadband: float = DIRECTION_DEADBAND,
) -> dict[str, Any]:
    metrics_df, windows_meta, aggregate_metrics = _evaluate_model_multi_window_core(
        backtest_df,
        model_key=model_key,
        window_count=window_count,
        window_size=window_size,
        window_step=window_step,
        deadband=deadband,
    )
    return {
        "model_key": model_key,
        "enabled": True,
        "window_config": {
            "evaluation_window_count": int(window_count),
            "evaluation_window_size": int(window_size),
            "evaluation_window_step": int(window_step),
        },
        "windows": windows_meta,
        "aggregate_metrics": aggregate_metrics,
        "window_metrics": metrics_df.to_dict(orient="records") if not metrics_df.empty else [],
    }


def evaluate_model_multi_window(
    backtest_df: pd.DataFrame,
    *,
    output_dir: str,
    model_key: str,
    window_count: int,
    window_size: int,
    window_step: int,
    deadband: float = DIRECTION_DEADBAND,
) -> dict[str, Any]:
    ensure_dirs([output_dir])

    metrics_df, windows_meta, aggregate_metrics = _evaluate_model_multi_window_core(
        backtest_df,
        model_key=model_key,
        window_count=window_count,
        window_size=window_size,
        window_step=window_step,
        deadband=deadband,
    )

    metrics_path = os.path.normpath(os.path.join(output_dir, "multi_window_metrics.csv"))
    summary_path = os.path.normpath(os.path.join(output_dir, "multi_window_summary.json"))

    metrics_df.to_csv(metrics_path, index=False)

    summary = {
        "model_key": model_key,
        "enabled": True,
        "window_config": {
            "evaluation_window_count": int(window_count),
            "evaluation_window_size": int(window_size),
            "evaluation_window_step": int(window_step),
        },
        "windows": windows_meta,
        "aggregate_metrics": aggregate_metrics,
        "artifacts": {
            "multi_window_metrics": metrics_path,
            "multi_window_summary": summary_path,
        },
    }
    save_json(summary, summary_path)
    logger.info(
        "Saved multi-window evaluation for model=%s windows=%s summary=%s",
        model_key,
        len(metrics_df),
        summary_path,
    )
    return summary


def _zscore(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() == 0:
        return pd.Series(np.zeros(len(values), dtype=float), index=values.index)
    mean_val = float(numeric.mean())
    std_val = float(numeric.std(ddof=0))
    if std_val <= 1e-12:
        return pd.Series(np.zeros(len(values), dtype=float), index=values.index)
    z = (numeric - mean_val) / std_val
    return z.fillna(0.0)


def build_global_multi_window_ranking(
    model_multi_window_summary: Dict[str, dict[str, Any]],
    *,
    ranking_metric: str,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for model_key, summary in model_multi_window_summary.items():
        agg = dict(summary.get("aggregate_metrics", {}) or {})
        if not agg:
            continue
        rows.append(
            {
                "model_key": model_key,
                "windows_evaluated": agg.get("windows_evaluated"),
                "window_count": agg.get("window_count", agg.get("windows_evaluated")),
                "mean_MAE": agg.get("mean_MAE"),
                "std_MAE": agg.get("std_MAE"),
                "mean_delta_vs_baseline": agg.get("mean_delta_vs_baseline"),
                "std_delta_vs_baseline": agg.get("std_delta_vs_baseline"),
                "mean_sign_accuracy_pct": agg.get("mean_sign_accuracy_pct"),
                "std_sign_accuracy_pct": agg.get("std_sign_accuracy_pct"),
                "mean_direction_accuracy_pct": agg.get("mean_direction_accuracy_pct"),
                "model_win_rate_vs_baseline": agg.get("model_win_rate_vs_baseline"),
                "win_rate_vs_baseline": agg.get("win_rate_vs_baseline", agg.get("model_win_rate_vs_baseline")),
                "worst_window_delta_vs_baseline": agg.get("worst_window_delta_vs_baseline"),
                "best_window_delta_vs_baseline": agg.get("best_window_delta_vs_baseline"),
            }
        )

    if not rows:
        return {
            "ranking_metric": ranking_metric,
            "model_count": 0,
            "ranking": [],
            "notes": "No eligible model multi-window summaries were found.",
        }

    ranking_df = pd.DataFrame(rows)
    ranking_df["_z_mean_delta"] = _zscore(ranking_df["mean_delta_vs_baseline"])
    ranking_df["_z_win_rate"] = _zscore(ranking_df["win_rate_vs_baseline"])

    std_delta = pd.to_numeric(ranking_df["std_delta_vs_baseline"], errors="coerce")
    if std_delta.notna().sum() == 0:
        std_delta = pd.Series(np.ones(len(ranking_df), dtype=float), index=ranking_df.index)
    else:
        fill_value = float(std_delta.dropna().max()) if std_delta.dropna().size else 1.0
        std_delta = std_delta.fillna(fill_value)
    ranking_df["_z_std_delta"] = _zscore(std_delta)

    ranking_df["robustness_score"] = (
        ranking_df["_z_mean_delta"]
        + ranking_df["_z_win_rate"]
        - ranking_df["_z_std_delta"]
    ).astype(float)

    ranking_metric_clean = str(ranking_metric or "robustness_score").strip()
    if ranking_metric_clean not in ranking_df.columns:
        ranking_metric_clean = "robustness_score"

    ascending_metrics = {
        "std_MAE",
        "std_delta_vs_baseline",
        "std_sign_accuracy_pct",
    }

    if ranking_metric_clean in ascending_metrics:
        ranking_df = ranking_df.sort_values(
            by=[ranking_metric_clean, "mean_delta_vs_baseline", "model_win_rate_vs_baseline"],
            ascending=[True, False, False],
            na_position="last",
        )
    else:
        ranking_df = ranking_df.sort_values(
            by=[ranking_metric_clean, "mean_delta_vs_baseline", "win_rate_vs_baseline"],
            ascending=[False, False, False],
            na_position="last",
        )

    median_std_delta = pd.to_numeric(ranking_df["std_delta_vs_baseline"], errors="coerce").median(skipna=True)
    robustness_classes: list[str] = []
    for _, row in ranking_df.iterrows():
        mean_delta = row.get("mean_delta_vs_baseline")
        win_rate = row.get("win_rate_vs_baseline")
        std_delta_val = row.get("std_delta_vs_baseline")

        if pd.notna(mean_delta) and pd.notna(win_rate) and pd.notna(std_delta_val):
            if float(mean_delta) > 0 and float(win_rate) >= 0.67 and (
                pd.isna(median_std_delta) or float(std_delta_val) <= float(median_std_delta)
            ):
                robustness_classes.append("stable")
            elif float(mean_delta) > 0 and float(win_rate) < 0.5:
                robustness_classes.append("lucky")
            elif float(mean_delta) <= 0 and float(win_rate) < 0.5:
                robustness_classes.append("unstable")
            else:
                robustness_classes.append("mixed")
        else:
            robustness_classes.append("unknown")
    ranking_df["robustness_class"] = robustness_classes

    ranking_rows: list[dict[str, Any]] = []
    for rank, (_, row) in enumerate(ranking_df.iterrows(), start=1):
        row_dict = {k: (None if pd.isna(v) else (float(v) if isinstance(v, (np.floating, float)) else int(v) if isinstance(v, (np.integer, int)) else v)) for k, v in row.items()}
        row_dict.pop("_z_mean_delta", None)
        row_dict.pop("_z_win_rate", None)
        row_dict.pop("_z_std_delta", None)
        row_dict["robustness_score"] = float(row_dict.get("robustness_score", 0.0))
        row_dict["rank"] = rank
        ranking_rows.append(row_dict)

    return {
        "ranking_metric": ranking_metric_clean,
        "model_count": int(len(ranking_rows)),
        "ranking": ranking_rows,
        "score_formula": "robustness_score = z(mean_delta_vs_baseline) + z(model_win_rate_vs_baseline) - z(std_delta_vs_baseline)",
    }


def save_global_multi_window_ranking(
    model_multi_window_summary: Dict[str, dict[str, Any]],
    *,
    output_path: str,
    ranking_metric: str,
) -> dict[str, Any]:
    output_dir = os.path.dirname(output_path)
    if output_dir:
        ensure_dirs([output_dir])
    ranking = build_global_multi_window_ranking(
        model_multi_window_summary,
        ranking_metric=ranking_metric,
    )
    save_json(ranking, output_path)
    logger.info("Saved global multi-window ranking to %s", output_path)
    return ranking
