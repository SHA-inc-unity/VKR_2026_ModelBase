from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict

import pandas as pd

from catboost_floader.core.config import (
    ENABLE_PARALLEL_CPU_BACKTEST,
    format_cpu_stage_policy_log,
    MULTI_AGGREGATED_DIR,
    MULTI_HORIZONS_HOURS,
    MULTI_PERSIST_AGGREGATED,
    MULTI_SKIP_TUNING,
    MULTI_TIMEFRAMES,
    apply_cpu_worker_limits,
    resolve_cpu_stage_parallel_policy,
)
from catboost_floader.core.utils import ensure_dirs, get_logger
from catboost_floader.features.engineering import build_direct_features, build_range_features

from catboost_floader.app.multi_model_task import _run_multi_model_key_task
from catboost_floader.app.pipeline_execution import _initialize_multi_model_worker

logger = get_logger("multi_model_orchestration")


def _run_multi_models(raw: pd.DataFrame) -> dict[str, Dict[str, Any]]:
    try:
        from catboost_floader.data.preprocessing import aggregate_for_modeling
    except Exception:
        try:
            from catboost_floader.data.preprocessing import _aggregate_for_modeling as aggregate_for_modeling
        except Exception:
            aggregate_for_modeling = None

    multi_models_summary: dict[str, Dict[str, Any]] = {}
    try:
        raw_prep = raw.copy()
        raw_prep["timestamp"] = pd.to_datetime(raw_prep["timestamp"], utc=True, errors="coerce")
        raw_prep = raw_prep.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        for col in [c for c in raw_prep.columns if c != "timestamp"]:
            raw_prep[col] = pd.to_numeric(raw_prep[col], errors="coerce")
        raw_prep = raw_prep.dropna(subset=["timestamp"]).ffill().bfill().dropna().reset_index(drop=True)

        if aggregate_for_modeling is not None:
            multi_tasks: list[Dict[str, Any]] = []
            for tf in MULTI_TIMEFRAMES:
                df_tf = aggregate_for_modeling(raw_prep, tf)
                if MULTI_PERSIST_AGGREGATED:
                    try:
                        ensure_dirs([MULTI_AGGREGATED_DIR])
                        csv_path = os.path.join(MULTI_AGGREGATED_DIR, f"market_aggregated_{tf}min.csv")
                        df_tf.to_csv(csv_path, index=False)
                    except Exception as _exc:
                        print(f"Failed to persist aggregated {tf}min dataset: {_exc}")
                direct_feats_tf = build_direct_features(df_tf)
                range_feats_tf = build_range_features(df_tf)

                for h in MULTI_HORIZONS_HOURS:
                    steps = int((h * 60) // tf)
                    if steps < 1:
                        continue
                    key = f"{tf}min_{h}h"
                    multi_tasks.append(
                        {
                            "key": key,
                            "tf": tf,
                            "hours": h,
                            "steps": steps,
                            "df_tf": df_tf,
                            "direct_features": direct_feats_tf,
                            "range_features": range_feats_tf,
                            "skip_tuning": MULTI_SKIP_TUNING,
                        }
                    )

            if multi_tasks:
                multi_policy = resolve_cpu_stage_parallel_policy(
                    "multi_model_evaluation",
                    parallel_units=len(multi_tasks),
                    granularity="model_key",
                    allow_parallel=ENABLE_PARALLEL_CPU_BACKTEST,
                )
                multi_workers = int(multi_policy["outer_workers"])
                multi_threads = int(multi_policy["inner_threads"])
                multi_parallel = bool(multi_policy["parallel_enabled"])
                keys_msg = ", ".join(task["key"] for task in multi_tasks)
                logger.info(
                    "Multi-model evaluation using CPU policy: %s keys=%s",
                    format_cpu_stage_policy_log(multi_policy),
                    keys_msg,
                )
                print(
                    "CPU policy (multi-model): "
                    f"{format_cpu_stage_policy_log(multi_policy)}"
                )

                multi_completed_in_parallel = False
                if multi_parallel:
                    apply_cpu_worker_limits(multi_threads)
                    try:
                        with ProcessPoolExecutor(
                            max_workers=multi_workers,
                            mp_context=mp.get_context("spawn"),
                            initializer=_initialize_multi_model_worker,
                            initargs=(multi_threads,),
                        ) as executor:
                            future_to_key = {
                                executor.submit(
                                    _run_multi_model_key_task,
                                    {**task, "catboost_thread_count": multi_threads, "outer_parallel_worker": True},
                                ): task["key"]
                                for task in multi_tasks
                            }
                            for future in as_completed(future_to_key):
                                key = future_to_key[future]
                                try:
                                    result = future.result()
                                    status = result.get("status")
                                    if status == "ok":
                                        multi_models_summary[key] = result["summary"]
                                        print(f"Multi-model: completed {key}")
                                    elif status == "skipped":
                                        print(f"Skipping {key}: {result.get('message')}")
                                    else:
                                        print(f"multi-model pipeline failed for {key}: {result.get('message')}")
                                except Exception as exc:
                                    print(f"multi-model pipeline failed for {key}: {exc}")
                        multi_completed_in_parallel = True
                    except Exception as exc:
                        logger.warning(
                            "CPU process pool unavailable for multi-model evaluation: %s. Falling back to sequential CPU path.",
                            exc,
                        )

                if not multi_completed_in_parallel:
                    logger.info(
                        "CPU-parallel multi-model evaluation disabled; using CPU sequential path for %s keys. reason=%s",
                        len(multi_tasks),
                        multi_policy.get("fallback_reason", "none"),
                    )
                    for task in multi_tasks:
                        key = task["key"]
                        print(f"Multi-model: processing {key}")
                        try:
                            result = _run_multi_model_key_task(
                                {**task, "catboost_thread_count": multi_threads, "outer_parallel_worker": False}
                            )
                            status = result.get("status")
                            if status == "ok":
                                multi_models_summary[key] = result["summary"]
                            elif status == "skipped":
                                print(f"Skipping {key}: {result.get('message')}")
                            else:
                                print(f"multi-model pipeline failed for {key}: {result.get('message')}")
                        except Exception as exc:
                            print(f"multi-model pipeline failed for {key}: {exc}")
    except Exception as exc:
        print(f"multi-model pipeline aborted: {exc}")

    if multi_models_summary:
        multi_models_summary = {key: multi_models_summary[key] for key in sorted(multi_models_summary)}
    return multi_models_summary
