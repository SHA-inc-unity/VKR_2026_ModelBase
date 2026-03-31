import math
import os

SYMBOL = "BTCUSDT"
BASE_TIMEFRAME = "1"
BYBIT_API_URL = "https://api.bybit.com"
BYBIT_CATEGORY = "linear"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "outputs")
ARTIFACTS_DIR = os.path.join(OUTPUT_DIR, "artifacts")
BACKTEST_DIR = os.path.join(OUTPUT_DIR, "backtest_results")
LOG_DIR = os.path.join(OUTPUT_DIR, "logs")
MODEL_DIR = os.path.join(OUTPUT_DIR, "models")
REPORT_DIR = os.path.join(OUTPUT_DIR, "reports")
CACHE_DIR = os.path.join(OUTPUT_DIR, "cache")

CACHE_ENABLED = True
CACHE_MAX_AGE_MINUTES = 10
DEFAULT_LOOKBACK_DAYS = 90
TRAIN_LOOKBACK_DAYS = 90
LIVE_LOOKBACK_DAYS = 14

REQUEST_LIMIT = 1000
REQUEST_SLEEP_SECONDS = 0.2
HTTP_TIMEOUT_SECONDS = 20

# Modeling timeframe: source data stay at 1m, modeling is performed on aggregated bars.
MODEL_TIMEFRAME_MINUTES = 5
DIRECT_HORIZON = 36  # 36 * 5m = 180m
SHORT_HORIZON = 6    # 30m
MEDIUM_HORIZON = 12  # 60m
RANGE_QUANTILES = (0.15, 0.85)
WIDE_RANGE_QUANTILES = (0.10, 0.90)
RANGE_BASELINE_ZSCORE = 1.0
TEST_SIZE = 0.2
RANDOM_SEED = 42

# Multi-window walk-forward evaluation settings.
# Window size and step are in backtest rows; set to 0 to auto-resolve from
# evaluation_window_count and available backtest length.
ENABLE_MULTI_WINDOW_EVALUATION = True
EVALUATION_WINDOW_COUNT = 6
EVALUATION_WINDOW_SIZE = 0
EVALUATION_WINDOW_STEP = 0
MULTI_WINDOW_RANKING_METRIC = "robustness_score"

# Multi-timeframe and multi-horizon configuration
# Timeframes to aggregate raw minute data to (minutes)
MULTI_TIMEFRAMES = [1, 5, 15, 60]
# Forecast horizons to train per timeframe (hours)
MULTI_HORIZONS_HOURS = [3, 6, 12]

# Multi-timeframe operational options
# When True, aggregated per-timeframe datasets will be persisted to disk (CSV)
MULTI_PERSIST_AGGREGATED = False
# Directory used for persisted aggregated datasets
MULTI_AGGREGATED_DIR = os.path.join(ARTIFACTS_DIR, "aggregated")
# Skip expensive hyperparameter tuning for multi-model runs (use sensible defaults / reuse params)
MULTI_SKIP_TUNING = True

# Quick calibration factor for movement magnitude predictions.
# Set to >1.0 to scale up movement outputs (useful for quick experiments).
MAGNITUDE_CALIBRATION = 1.0

USE_HALVING_SEARCH = True
HALVING_MAX_ROWS = 18000
TIME_SERIES_SPLITS = 4
HALVING_FACTOR = 4

# CUDA/GPU support. Keep disabled by default for portability.
# Disabled temporarily to avoid excessive GPU memory reservation during local runs.
USE_GPU = False
GPU_DEVICES = "0"
GPU_RAM_PART = 0.85

DIRECT_CATBOOST_PARAMS = {
    "iterations": 500,
    "learning_rate": 0.05,
    "depth": 8,
    "loss_function": "RMSE",
    "eval_metric": "RMSE",
    "verbose": False,
    "random_seed": RANDOM_SEED,
}

# Deadband threshold for direction labeling (absolute relative return).
# If |target_return| < DIRECTION_DEADBAND then label is treated as neutral (0).
# Default set to 0.0005 (0.05%). Tune if needed.
DIRECTION_DEADBAND = 0.0005

# Confidence threshold for discrete direction predictions from the classifier.
# If the classifier's max class probability < this threshold, the direction is treated as neutral.
DIRECTION_PRED_THRESHOLD = 0.6

# Direct prediction composition defaults. These control how direction confidence,
# low-confidence fallback, and strategy blending behave at inference time.
DIRECT_COMPOSITION_DEFAULTS = {
    "profile_enabled": True,
    "high_confidence_sign_mode": "label",
    "high_confidence_label_weight": 1.0,
    "high_confidence_expectation_weight": 0.0,
    "label_confidence_threshold": DIRECTION_PRED_THRESHOLD,
    "low_confidence_sign_mode": "neutral",
    "low_confidence_label_weight": 0.0,
    "low_confidence_expectation_weight": 1.0,
    "expectation_deadband": 0.0,
    "expectation_power": 1.0,
    "movement_scale": 1.0,
    "anomaly_magnitude_floor": 0.2,
    "strategy_alpha_grid": [0.25, 0.4, 0.55, 0.7, 0.85],
    "strategy_baselines": ["persistence", "rolling_mean"],
    "strategy_allow_baseline_only": True,
    "strategy_prefer_model_tolerance": 0.0,
    "strategy_persistence_guard_tolerance": 0.005,
    "profile_min_relative_improvement_vs_default": 0.001,
    "profile_disable_relative_gap_vs_default": 0.0,
    "profile_fallbacks": [],
}

# Robustness-aware direct strategy selection.
# These thresholds are used during validation-time strategy/profile selection,
# in addition to single-snapshot MAE checks.
DIRECT_STRATEGY_ROBUSTNESS_ENABLED = True
DIRECT_STRATEGY_ROBUSTNESS_REQUIRED_FOR_NON_DEFAULT = True
DIRECT_STRATEGY_ROBUSTNESS_WINDOW_COUNT = 5
DIRECT_STRATEGY_ROBUSTNESS_WINDOW_SIZE = 0
DIRECT_STRATEGY_ROBUSTNESS_WINDOW_STEP = 0
DIRECT_STRATEGY_ROBUSTNESS_MIN_MEAN_DELTA_VS_BASELINE = 0.0
DIRECT_STRATEGY_ROBUSTNESS_MIN_WIN_RATE_VS_BASELINE = 0.50
DIRECT_STRATEGY_ROBUSTNESS_MAX_STD_DELTA_VS_BASELINE = 25.0
DIRECT_STRATEGY_ROBUSTNESS_MIN_SIGN_ACCURACY_PCT = 49.0
DIRECT_STRATEGY_ROBUSTNESS_MAX_LOSING_WINDOWS = 3
# If candidate MAEs are close, prefer the one with stronger robustness tuple.
DIRECT_STRATEGY_ROBUSTNESS_MAE_TOLERANCE_RATIO = 0.002

# Robustness regime classification and practical-selection hard gating.
# Statuses are emitted as machine-readable fields per model/regime:
# robust_winner, snapshot_winner_unstable, near_baseline, degraded, deadweight, disabled.
ROBUSTNESS_REGIME_CLASSIFICATION_ENABLED = True
ROBUSTNESS_REGIME_DOWNGRADE_DEGRADED_SELECTION = True
ROBUSTNESS_REGIME_DISABLE_ENABLED = True
ROBUSTNESS_REGIME_DISABLE_DEADWEIGHT = True
ROBUSTNESS_REGIME_DISABLE_PERSISTENT_LOSER = True
ROBUSTNESS_REGIME_DISABLE_NEGATIVE_SNAPSHOT_AND_POOR_ROBUSTNESS = True
ROBUSTNESS_REGIME_DISABLE_LOW_WIN_RATE_HIGH_STD = True

ROBUSTNESS_STATUS_ROBUST_MIN_MEAN_DELTA = 0.0
ROBUSTNESS_STATUS_ROBUST_MIN_WIN_RATE = 0.50
ROBUSTNESS_STATUS_ROBUST_MAX_STD_DELTA = 25.0
ROBUSTNESS_STATUS_ROBUST_MIN_SNAPSHOT_DELTA = 0.0

ROBUSTNESS_STATUS_SNAPSHOT_WINNER_MIN_SNAPSHOT_DELTA = 0.0
ROBUSTNESS_STATUS_SNAPSHOT_UNSTABLE_MIN_STD_DELTA = 30.0
ROBUSTNESS_STATUS_SNAPSHOT_UNSTABLE_MAX_WIN_RATE = 0.85

ROBUSTNESS_STATUS_NEAR_BASELINE_MAX_ABS_MEAN_DELTA = 1.0
ROBUSTNESS_STATUS_NEAR_BASELINE_MAX_ABS_SNAPSHOT_DELTA = 1.0

ROBUSTNESS_STATUS_DEADWEIGHT_MAX_ABS_MEAN_DELTA = 0.1
ROBUSTNESS_STATUS_DEADWEIGHT_MAX_ABS_SNAPSHOT_DELTA = 0.1
ROBUSTNESS_STATUS_DEADWEIGHT_MAX_WIN_RATE = 0.05

ROBUSTNESS_STATUS_DEGRADED_MAX_MEAN_DELTA = -1.0
ROBUSTNESS_STATUS_DEGRADED_MAX_SNAPSHOT_DELTA = -1.0
ROBUSTNESS_STATUS_DEGRADED_MAX_WIN_RATE = 0.50

ROBUSTNESS_DISABLE_PERSISTENT_LOSER_MAX_WIN_RATE = 0.34
ROBUSTNESS_DISABLE_PERSISTENT_LOSER_MAX_MEAN_DELTA = -8.0
ROBUSTNESS_DISABLE_NEGATIVE_SNAPSHOT_THRESHOLD = -1.0
ROBUSTNESS_DISABLE_POOR_MEAN_DELTA_THRESHOLD = -2.0
ROBUSTNESS_DISABLE_POOR_WIN_RATE_THRESHOLD = 0.50
ROBUSTNESS_DISABLE_LOW_WIN_RATE_THRESHOLD = 0.35
ROBUSTNESS_DISABLE_HIGH_STD_DELTA_THRESHOLD = 35.0

# Conservative final holdout safeguard for main pipeline only.
# If selected strategy is only marginally below persistence on the final slice,
# fallback to persistence for safer practical behavior.
MAIN_HOLDOUT_SAFEGUARD_ENABLED = True
MAIN_HOLDOUT_SAFEGUARD_MODEL_KEY = "main_direct_pipeline"
MAIN_HOLDOUT_SAFEGUARD_MIN_POINTS = 500
MAIN_HOLDOUT_SAFEGUARD_MAX_RELATIVE_UNDERPERFORMANCE = 0.003

# Post-screening acceleration flags. These intentionally target only the heavy
# evaluation stage after fast screening and the backtest math path. GPU is kept
# for compatibility only and is disabled by default because CPU-parallel
# evaluation is faster on the current datasets.
ENABLE_GPU_FULL_EVALUATION = False
ENABLE_GPU_BACKTEST = False
GPU_FULL_EVALUATION_DEVICE = GPU_DEVICES
GPU_BACKTEST_DEVICE = GPU_DEVICES

# CPU-parallel execution policy for the heavy post-screening stage. Defaults are
# tuned for a 16-core / 32-thread machine while remaining safe on smaller hosts.
CPU_LOGICAL_THREADS = max(1, os.cpu_count() or 1)
MAX_CPU_UTILIZATION_MODE = "aggressive"

# Centralized CPU policy. All heavy stages should resolve their runtime execution
# plan from these stage-level settings instead of ad-hoc worker/thread values.
CPU_PARALLEL_TARGET_THREADS = 32
CPU_PARALLEL_ENABLE_NESTED_PARALLEL = True
CPU_PARALLEL_PARALLEL_UNITS_TARGET = 32
CPU_PARALLEL_ALLOW_OVERSUBSCRIPTION = True
CPU_PARALLEL_OVERSUBSCRIPTION_FACTOR = 1.25
CPU_PARALLEL_MAX_THREADS = max(1, min(CPU_PARALLEL_TARGET_THREADS, CPU_LOGICAL_THREADS))

ENABLE_PARALLEL_CPU_FULL_EVALUATION = True
ENABLE_PARALLEL_CPU_BACKTEST = True

FAST_SCREENING_TARGET_THREADS = CPU_PARALLEL_TARGET_THREADS
FAST_SCREENING_OUTER_WORKERS = 16
FAST_SCREENING_INNER_THREADS = 8
FAST_SCREENING_GRANULARITY = "candidate"
FAST_SCREENING_ENABLE_NESTED_PARALLEL = True
FAST_SCREENING_PARALLEL_UNITS_TARGET = 32

STAGE2_PARALLEL_MODE = "adaptive_cpu"
STAGE2_PARALLEL_GRANULARITY = "candidate_fold"
STAGE2_ENABLE_NESTED_PARALLEL = True
STAGE2_TARGET_CPU_THREADS = CPU_PARALLEL_TARGET_THREADS
STAGE2_OUTER_WORKERS = 16
STAGE2_INNER_THREADS = 8
STAGE2_PARALLEL_UNITS_TARGET = 32

BACKTEST_TARGET_THREADS = CPU_PARALLEL_TARGET_THREADS
BACKTEST_OUTER_WORKERS = 1
BACKTEST_INNER_THREADS = 0
BACKTEST_GRANULARITY = "vectorized_batch"
BACKTEST_ENABLE_NESTED_PARALLEL = True
BACKTEST_PARALLEL_UNITS_TARGET = 32

BACKTEST_WINDOW_TARGET_THREADS = CPU_PARALLEL_TARGET_THREADS
BACKTEST_WINDOW_OUTER_WORKERS = 16
BACKTEST_WINDOW_INNER_THREADS = 8
BACKTEST_WINDOW_GRANULARITY = "backtest_window"
BACKTEST_WINDOW_ENABLE_NESTED_PARALLEL = True
BACKTEST_WINDOW_PARALLEL_UNITS_TARGET = 32

MULTI_MODEL_TARGET_THREADS = CPU_PARALLEL_TARGET_THREADS
MULTI_MODEL_OUTER_WORKERS = 16
MULTI_MODEL_INNER_THREADS = 8
MULTI_MODEL_GRANULARITY = "model_key"
MULTI_MODEL_ENABLE_NESTED_PARALLEL = True
MULTI_MODEL_PARALLEL_UNITS_TARGET = 32

# Backward-compatible aliases used by existing code paths.
PARALLEL_EVAL_WORKERS = STAGE2_OUTER_WORKERS
PARALLEL_BACKTEST_WORKERS = BACKTEST_OUTER_WORKERS
PARALLEL_MULTI_MODEL_WORKERS = MULTI_MODEL_OUTER_WORKERS
CATBOOST_THREADS_PER_WORKER = max(1, STAGE2_INNER_THREADS)

CPU_STAGE_POLICIES = {
    "fast_screening": {
        "target_threads": FAST_SCREENING_TARGET_THREADS,
        "outer_workers": FAST_SCREENING_OUTER_WORKERS,
        "inner_threads": FAST_SCREENING_INNER_THREADS,
        "granularity": FAST_SCREENING_GRANULARITY,
        "enable_nested_parallel": FAST_SCREENING_ENABLE_NESTED_PARALLEL,
        "parallel_units_target": FAST_SCREENING_PARALLEL_UNITS_TARGET,
        "allow_oversubscription": CPU_PARALLEL_ALLOW_OVERSUBSCRIPTION,
        "oversubscription_factor": CPU_PARALLEL_OVERSUBSCRIPTION_FACTOR,
    },
    "stage2_full_evaluation": {
        "target_threads": STAGE2_TARGET_CPU_THREADS,
        "outer_workers": STAGE2_OUTER_WORKERS,
        "inner_threads": STAGE2_INNER_THREADS,
        "granularity": STAGE2_PARALLEL_GRANULARITY,
        "enable_nested_parallel": STAGE2_ENABLE_NESTED_PARALLEL,
        "parallel_units_target": STAGE2_PARALLEL_UNITS_TARGET,
        "allow_oversubscription": CPU_PARALLEL_ALLOW_OVERSUBSCRIPTION,
        "oversubscription_factor": CPU_PARALLEL_OVERSUBSCRIPTION_FACTOR,
    },
    "backtest": {
        "target_threads": BACKTEST_TARGET_THREADS,
        "outer_workers": BACKTEST_OUTER_WORKERS,
        "inner_threads": BACKTEST_INNER_THREADS,
        "granularity": BACKTEST_GRANULARITY,
        "enable_nested_parallel": BACKTEST_ENABLE_NESTED_PARALLEL,
        "parallel_units_target": BACKTEST_PARALLEL_UNITS_TARGET,
        "allow_oversubscription": False,
        "oversubscription_factor": 1.0,
    },
    "backtest_window_evaluation": {
        "target_threads": BACKTEST_WINDOW_TARGET_THREADS,
        "outer_workers": BACKTEST_WINDOW_OUTER_WORKERS,
        "inner_threads": BACKTEST_WINDOW_INNER_THREADS,
        "granularity": BACKTEST_WINDOW_GRANULARITY,
        "enable_nested_parallel": BACKTEST_WINDOW_ENABLE_NESTED_PARALLEL,
        "parallel_units_target": BACKTEST_WINDOW_PARALLEL_UNITS_TARGET,
        "allow_oversubscription": CPU_PARALLEL_ALLOW_OVERSUBSCRIPTION,
        "oversubscription_factor": CPU_PARALLEL_OVERSUBSCRIPTION_FACTOR,
    },
    "multi_model_evaluation": {
        "target_threads": MULTI_MODEL_TARGET_THREADS,
        "outer_workers": MULTI_MODEL_OUTER_WORKERS,
        "inner_threads": MULTI_MODEL_INNER_THREADS,
        "granularity": MULTI_MODEL_GRANULARITY,
        "enable_nested_parallel": MULTI_MODEL_ENABLE_NESTED_PARALLEL,
        "parallel_units_target": MULTI_MODEL_PARALLEL_UNITS_TARGET,
        "allow_oversubscription": CPU_PARALLEL_ALLOW_OVERSUBSCRIPTION,
        "oversubscription_factor": CPU_PARALLEL_OVERSUBSCRIPTION_FACTOR,
    },
}

CPU_WORKER_THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
)

# Focused calibration profile for the current strongest candidate. This keeps
# more usable directional signal when the classifier is uncertain and avoids
# persistence-heavy blends unless they are clearly needed. Profiles support a
# simple inheritance chain through the optional "inherits" field.
DIRECT_COMPOSITION_PROFILES = {
    "default": {},
    "main_direct_pipeline": {
        "inherits": "default",
        "label_confidence_threshold": DIRECTION_PRED_THRESHOLD,
        "low_confidence_sign_mode": "neutral",
        "low_confidence_label_weight": 0.0,
        "low_confidence_expectation_weight": 1.0,
        "expectation_deadband": 0.0,
        "movement_scale": 1.0,
        "strategy_alpha_grid": [0.25, 0.4, 0.55, 0.7, 0.85],
        "strategy_baselines": ["persistence", "rolling_mean"],
        "strategy_allow_baseline_only": True,
        "strategy_prefer_model_tolerance": 0.0,
        "profile_min_relative_improvement_vs_default": 0.001,
        "profile_fallbacks": ["default"],
    },
    "60min_family": {
        "inherits": "main_direct_pipeline",
        "label_confidence_threshold": 0.55,
        "low_confidence_sign_mode": "blend",
        "low_confidence_label_weight": 0.0,
        "low_confidence_expectation_weight": 0.65,
        "expectation_deadband": 0.05,
        "expectation_power": 1.0,
        "movement_scale": 1.0,
        "strategy_alpha_grid": [0.25, 0.4, 0.55, 0.7, 0.85, 0.92],
        "strategy_allow_baseline_only": True,
        "strategy_prefer_model_tolerance": 0.0005,
        "profile_min_relative_improvement_vs_default": 0.001,
        "profile_fallbacks": ["default", "main_direct_pipeline"],
    },
    "60min_3h": {
        "inherits": "60min_family",
        "low_confidence_sign_mode": "expectation",
        "low_confidence_expectation_weight": 1.0,
        "movement_scale": 1.0,
        "strategy_alpha_grid": [0.4, 0.55, 0.7, 0.85, 0.92],
        "strategy_allow_baseline_only": True,
        "strategy_prefer_model_tolerance": 0.0005,
        "profile_fallbacks": ["60min_family", "default", "main_direct_pipeline"],
    },
    "60min_6h": {
        "inherits": "60min_family",
        "label_confidence_threshold": 0.56,
        "expectation_deadband": 0.04,
        "movement_scale": 0.99,
        "strategy_alpha_grid": [0.25, 0.4, 0.55, 0.7, 0.85],
        "strategy_allow_baseline_only": True,
        "strategy_prefer_model_tolerance": 0.0,
        "profile_fallbacks": ["60min_family", "default", "main_direct_pipeline"],
    },
    "60min_12h": {
        "inherits": "60min_family",
        "label_confidence_threshold": 0.58,
        "low_confidence_sign_mode": "neutral",
        "expectation_deadband": 0.04,
        "movement_scale": 0.98,
        "strategy_alpha_grid": [0.25, 0.4, 0.55, 0.7, 0.85],
        "strategy_allow_baseline_only": True,
        "strategy_prefer_model_tolerance": 0.0,
        "profile_fallbacks": ["60min_family", "default", "main_direct_pipeline"],
    },
}

# When anomalies are detected, shrink predicted movement magnitudes by this factor proportional
# to the anomaly score (e.g. 0.5 reduces magnitude up to ~50% when anomaly_score=1.0).
ANOMALY_MAGNITUDE_SHRINK = 0.5

RANGE_LOW_CATBOOST_PARAMS = {
    "iterations": 500,
    "learning_rate": 0.05,
    "depth": 8,
    "loss_function": f"Quantile:alpha={RANGE_QUANTILES[0]}",
    "eval_metric": f"Quantile:alpha={RANGE_QUANTILES[0]}",
    "verbose": False,
    "random_seed": RANDOM_SEED,
}

RANGE_HIGH_CATBOOST_PARAMS = {
    "iterations": 500,
    "learning_rate": 0.05,
    "depth": 8,
    "loss_function": f"Quantile:alpha={RANGE_QUANTILES[1]}",
    "eval_metric": f"Quantile:alpha={RANGE_QUANTILES[1]}",
    "verbose": False,
    "random_seed": RANDOM_SEED,
}

DIRECT_SEARCH_GRID = {
    "depth": [6, 8, 10],
    "learning_rate": [0.01, 0.02, 0.03, 0.05],
    "l2_leaf_reg": [3, 5, 7, 9, 11],
    "iterations": [300, 500, 800, 1200],
    "border_count": [64, 128, 254],
}

RANGE_SEARCH_GRID = {
    "depth": [6, 8, 10],
    "learning_rate": [0.01, 0.02, 0.03, 0.05],
    "l2_leaf_reg": [5, 7, 9, 11, 13],
    "iterations": [300, 500, 800, 1200],
    "border_count": [64, 128, 254],
}

ANOMALY_RETURN_Z = 4.0
ANOMALY_VOLUME_Z = 4.0
ANOMALY_SPREAD_Z = 4.0
ANOMALY_ORDERBOOK_Z = 4.0
ANOMALY_GRACE_MINUTES = 15
SEVERE_ANOMALY_SCORE = 0.85

OOD_ZSCORE_CLIP = 8.0

ENABLE_LIVE_ORDERBOOK_FEATURES = True
ORDERBOOK_DEPTH_LIMIT = 50

# Backward-compatible aliases
RANGE_CATBOOST_PARAMS = RANGE_LOW_CATBOOST_PARAMS

SQLITE_DB_PATH = os.path.join(OUTPUT_DIR, "market_data.sqlite")
MARKET_DATASET_CACHE_MINUTES = 5


def apply_hardware_params(params: dict) -> dict:
    out = dict(params)
    if USE_GPU:
        out.setdefault("task_type", "GPU")
        out.setdefault("devices", GPU_DEVICES)
        out.setdefault("gpu_ram_part", GPU_RAM_PART)
    return out


def _resolve_stage_cpu_defaults(stage: str) -> tuple[str, dict]:
    stage_key = str(stage or "").strip().lower()
    if stage_key not in CPU_STAGE_POLICIES:
        stage_key = "stage2_full_evaluation"
    return stage_key, dict(CPU_STAGE_POLICIES[stage_key])


def resolve_cpu_stage_parallel_policy(
    stage: str,
    *,
    parallel_units: int,
    granularity: str | None = None,
    nested_outer_parallel: bool = False,
    nested_thread_count: int | None = None,
    allow_parallel: bool = True,
    configured_outer_workers: int | None = None,
    configured_inner_threads: int | None = None,
    configured_target_threads: int | None = None,
) -> dict:
    stage_key, stage_policy = _resolve_stage_cpu_defaults(stage)
    units = max(1, int(parallel_units or 1))

    target_requested = max(1, int(configured_target_threads or stage_policy.get("target_threads") or CPU_PARALLEL_TARGET_THREADS))
    target_threads = max(1, min(target_requested, CPU_LOGICAL_THREADS))
    fallback_reasons: list[str] = []

    if target_threads < target_requested:
        fallback_reasons.append(
            f"host_limit:{CPU_LOGICAL_THREADS}_threads_below_target:{target_requested}"
        )

    nested_limit = int(nested_thread_count or 0)
    if nested_limit > 0 and nested_outer_parallel:
        clamped_target = min(target_threads, nested_limit)
        if clamped_target < target_threads:
            fallback_reasons.append(
                f"nested_worker_budget_limit:{nested_limit}_threads"
            )
        target_threads = clamped_target

    requested_outer = int(configured_outer_workers or stage_policy.get("outer_workers") or target_threads)
    if requested_outer <= 0:
        requested_outer = target_threads

    requested_inner = int(configured_inner_threads or stage_policy.get("inner_threads") or 0)
    requested_granularity = str(granularity or stage_policy.get("granularity") or "candidate")
    parallel_units_target = max(1, int(stage_policy.get("parallel_units_target") or CPU_PARALLEL_PARALLEL_UNITS_TARGET))

    if units < parallel_units_target:
        fallback_reasons.append(
            f"parallel_units:{units}_below_target:{parallel_units_target}"
        )

    nested_enabled = bool(
        CPU_PARALLEL_ENABLE_NESTED_PARALLEL
        and stage_policy.get("enable_nested_parallel", True)
        and not nested_outer_parallel
    )
    if nested_outer_parallel:
        fallback_reasons.append("running_inside_outer_parallel_worker")

    parallel_requested = bool(allow_parallel)
    if not parallel_requested:
        fallback_reasons.append("parallel_disabled_for_stage")

    outer_workers = 1
    inner_threads = max(1, requested_inner or target_threads)
    oversub_enabled = False

    if parallel_requested and nested_enabled and units > 1:
        outer_workers = max(1, min(units, requested_outer, target_threads))
        if outer_workers < requested_outer:
            fallback_reasons.append(
                f"outer_workers_limited_to:{outer_workers}"
            )

        if requested_inner > 0:
            inner_threads = max(1, requested_inner)
        else:
            inner_threads = max(1, math.ceil(target_threads / max(1, outer_workers)))

        oversub_enabled = bool(stage_policy.get("allow_oversubscription", False))
        oversub_factor = float(stage_policy.get("oversubscription_factor") or 1.0)
        max_budget = target_threads
        if oversub_enabled:
            max_budget = max(target_threads, int(math.ceil(target_threads * max(1.0, oversub_factor))))

        while outer_workers * inner_threads < target_threads and outer_workers * (inner_threads + 1) <= max_budget:
            inner_threads += 1

        while outer_workers * inner_threads > max_budget and inner_threads > 1:
            inner_threads -= 1

    parallel_enabled = bool(parallel_requested and nested_enabled and outer_workers > 1 and units > 1)
    if not parallel_enabled:
        outer_workers = 1
        inner_threads = max(1, min(target_threads, requested_inner or target_threads))
        oversub_enabled = False

    estimated_budget = outer_workers * inner_threads if parallel_enabled else inner_threads
    full_target_reached = estimated_budget >= target_requested
    fallback_reason = "none"
    if not full_target_reached:
        fallback_reason = ";".join(dict.fromkeys(fallback_reasons)) or "insufficient_parallel_capacity"

    return {
        "stage": stage_key,
        "mode": "parallel_cpu" if parallel_enabled else "sequential_cpu",
        "parallel_enabled": parallel_enabled,
        "granularity": requested_granularity,
        "nested_enabled": nested_enabled and parallel_enabled,
        "oversubscription_enabled": oversub_enabled,
        "target_threads_requested": target_requested,
        "target_threads": target_threads,
        "host_threads": CPU_LOGICAL_THREADS,
        "outer_workers": outer_workers,
        "inner_threads": inner_threads,
        "parallel_units": units,
        "parallel_units_target": parallel_units_target,
        "estimated_cpu_budget": max(1, estimated_budget),
        "fallback_reasons": list(dict.fromkeys(fallback_reasons)),
        "fallback_reason": fallback_reason,
        "full_target_reached": full_target_reached,
    }


def format_cpu_stage_policy_log(policy: dict) -> str:
    return (
        f"target_threads={policy.get('target_threads_requested')} "
        f"effective_target={policy.get('target_threads')} "
        f"outer_workers={policy.get('outer_workers')} "
        f"inner_threads={policy.get('inner_threads')} "
        f"parallel_units={policy.get('parallel_units')} "
        f"granularity={policy.get('granularity')} "
        f"nested={policy.get('nested_enabled')} "
        f"oversubscription={policy.get('oversubscription_enabled')} "
        f"fallback_reason={policy.get('fallback_reason') or 'none'}"
    )


def resolve_parallel_cpu_settings(total_tasks: int, configured_workers: int) -> tuple[int, int]:
    policy = resolve_cpu_stage_parallel_policy(
        "multi_model_evaluation",
        parallel_units=max(1, int(total_tasks or 1)),
        allow_parallel=ENABLE_PARALLEL_CPU_BACKTEST,
        configured_outer_workers=max(1, int(configured_workers or 1)),
    )
    return int(policy["outer_workers"]), int(policy["inner_threads"])


def resolve_stage2_parallel_policy(candidate_count: int, fold_count: int, *, nested_outer_parallel: bool = False) -> dict:
    candidates = max(1, int(candidate_count or 1))
    folds = max(1, int(fold_count or 1))

    requested_mode = str(STAGE2_PARALLEL_MODE or "adaptive_cpu").lower()
    requested_granularity = str(STAGE2_PARALLEL_GRANULARITY or "candidate_fold").lower()

    if requested_mode not in {"adaptive_cpu", "candidate_cpu", "candidate_fold_cpu", "sequential_cpu"}:
        requested_mode = "adaptive_cpu"

    if requested_granularity not in {"adaptive", "candidate", "model", "candidate_fold", "fold"}:
        requested_granularity = "candidate_fold"

    granularity = requested_granularity
    if requested_mode == "candidate_cpu":
        granularity = "candidate"
    elif requested_mode == "candidate_fold_cpu":
        granularity = "candidate_fold"

    if granularity == "adaptive":
        granularity = "candidate_fold" if folds > 1 else "candidate"
    if granularity == "model":
        granularity = "candidate"
    if granularity == "fold":
        granularity = "candidate_fold"

    parallel_units = candidates * folds if granularity == "candidate_fold" else candidates
    policy = resolve_cpu_stage_parallel_policy(
        "stage2_full_evaluation",
        parallel_units=parallel_units,
        granularity=granularity,
        nested_outer_parallel=nested_outer_parallel,
        nested_thread_count=current_worker_thread_count() if nested_outer_parallel else None,
        allow_parallel=ENABLE_PARALLEL_CPU_FULL_EVALUATION and requested_mode != "sequential_cpu",
        configured_outer_workers=STAGE2_OUTER_WORKERS,
        configured_inner_threads=STAGE2_INNER_THREADS,
        configured_target_threads=STAGE2_TARGET_CPU_THREADS,
    )

    policy["mode"] = requested_mode if policy["parallel_enabled"] else "sequential_cpu"
    policy["candidate_count"] = candidates
    policy["fold_count"] = folds
    return policy


def apply_cpu_worker_limits(thread_count: int, *, mark_outer_parallel: bool = False) -> int:
    safe_threads = max(1, int(thread_count or 1))
    thread_str = str(safe_threads)
    for env_var in CPU_WORKER_THREAD_ENV_VARS:
        os.environ[env_var] = thread_str
    os.environ["CATBOOST_WORKER_THREADS"] = thread_str
    os.environ["CATBOOST_OUTER_PARALLEL"] = "1" if mark_outer_parallel else "0"
    return safe_threads


def current_worker_thread_count() -> int | None:
    raw = os.environ.get("CATBOOST_WORKER_THREADS")
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def is_nested_outer_parallel() -> bool:
    return os.environ.get("CATBOOST_OUTER_PARALLEL", "0") == "1"
