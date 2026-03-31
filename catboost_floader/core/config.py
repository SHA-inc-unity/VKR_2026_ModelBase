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
MAX_CPU_UTILIZATION_MODE = "balanced"
ENABLE_PARALLEL_CPU_FULL_EVALUATION = True
ENABLE_PARALLEL_CPU_BACKTEST = True
PARALLEL_EVAL_WORKERS = max(1, min(4, CPU_LOGICAL_THREADS))
PARALLEL_BACKTEST_WORKERS = max(1, min(8, CPU_LOGICAL_THREADS))
PARALLEL_MULTI_MODEL_WORKERS = max(1, min(8, CPU_LOGICAL_THREADS))
CATBOOST_THREADS_PER_WORKER = CPU_LOGICAL_THREADS
STAGE2_PARALLEL_MODE = "adaptive_cpu"
STAGE2_PARALLEL_GRANULARITY = "adaptive"
STAGE2_ENABLE_NESTED_PARALLEL = True
STAGE2_TARGET_CPU_THREADS = max(1, min(32, CPU_LOGICAL_THREADS))
STAGE2_OUTER_WORKERS = 0
STAGE2_INNER_THREADS = 0
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


def resolve_parallel_cpu_settings(total_tasks: int, configured_workers: int) -> tuple[int, int]:
    tasks = max(1, int(total_tasks or 1))
    workers = max(1, min(tasks, int(configured_workers or 1), CPU_LOGICAL_THREADS))
    threads = max(1, min(CATBOOST_THREADS_PER_WORKER, max(1, CPU_LOGICAL_THREADS // workers)))
    return workers, threads


def resolve_stage2_parallel_policy(candidate_count: int, fold_count: int, *, nested_outer_parallel: bool = False) -> dict:
    candidates = max(1, int(candidate_count or 1))
    folds = max(1, int(fold_count or 1))
    target_threads = max(1, min(STAGE2_TARGET_CPU_THREADS, CPU_LOGICAL_THREADS))
    configured_outer = max(1, int(STAGE2_OUTER_WORKERS or target_threads))
    configured_inner = int(STAGE2_INNER_THREADS or 0)
    requested_mode = str(STAGE2_PARALLEL_MODE or "adaptive_cpu").lower()
    requested_granularity = str(STAGE2_PARALLEL_GRANULARITY or "adaptive").lower()
    nested_enabled = bool(STAGE2_ENABLE_NESTED_PARALLEL and not nested_outer_parallel)

    if requested_mode not in {"adaptive_cpu", "candidate_cpu", "candidate_fold_cpu", "sequential_cpu"}:
        requested_mode = "adaptive_cpu"

    if requested_granularity not in {"adaptive", "candidate", "model", "candidate_fold", "fold"}:
        requested_granularity = "adaptive"

    granularity = requested_granularity
    if requested_mode == "candidate_cpu":
        granularity = "candidate"
    elif requested_mode == "candidate_fold_cpu":
        granularity = "candidate_fold"
    if granularity == "adaptive":
        granularity = "candidate_fold" if nested_enabled and folds > 1 and candidates < min(configured_outer, target_threads) else "candidate"
    if granularity == "model":
        granularity = "candidate"
    if granularity == "fold":
        granularity = "candidate_fold"

    parallel_requested = ENABLE_PARALLEL_CPU_FULL_EVALUATION and requested_mode != "sequential_cpu"

    if not parallel_requested or not nested_enabled:
        outer_workers = 1
        inner_threads = max(1, configured_inner or target_threads)
        if nested_outer_parallel:
            inner_threads = max(1, min(inner_threads, target_threads))
        parallel_units = candidates
        mode = "sequential_cpu" if not parallel_requested else "nested_sequential_cpu"
        granularity = "candidate"
    else:
        parallel_units = candidates * folds if granularity == "candidate_fold" else candidates
        outer_workers = max(1, min(parallel_units, configured_outer, target_threads))
        if configured_inner > 0:
            inner_threads = max(1, configured_inner)
        else:
            inner_threads = max(1, target_threads // outer_workers)
        while outer_workers * inner_threads > target_threads and inner_threads > 1:
            inner_threads -= 1
        mode = requested_mode

    estimated_threads = max(1, outer_workers * inner_threads)
    parallel_enabled = bool(parallel_requested and outer_workers > 1 and parallel_units > 1 and nested_enabled)

    return {
        "mode": mode if parallel_enabled else "sequential_cpu",
        "parallel_enabled": parallel_enabled,
        "granularity": granularity,
        "nested_enabled": nested_enabled and parallel_enabled,
        "outer_workers": outer_workers if parallel_enabled else 1,
        "inner_threads": inner_threads,
        "target_threads": target_threads,
        "candidate_count": candidates,
        "fold_count": folds,
        "parallel_units": parallel_units,
        "estimated_cpu_budget": estimated_threads if parallel_enabled else inner_threads,
    }


def apply_cpu_worker_limits(thread_count: int, *, mark_outer_parallel: bool = False) -> int:
    safe_threads = max(1, int(thread_count or 1))
    thread_str = str(safe_threads)
    for env_var in CPU_WORKER_THREAD_ENV_VARS:
        os.environ[env_var] = thread_str
    os.environ["CATBOOST_WORKER_THREADS"] = thread_str
    if mark_outer_parallel:
        os.environ["CATBOOST_OUTER_PARALLEL"] = "1"
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
