import os

from catboost_floader.core.parallel_policy import (
    CATBOOST_THREADS_PER_WORKER,
    CPU_LOGICAL_THREADS,
    CPU_PARALLEL_MAX_THREADS,
    CPU_PARALLEL_TARGET_THREADS,
    ENABLE_PARALLEL_CPU_BACKTEST,
    ENABLE_PARALLEL_CPU_FAST_SCREENING,
    ENABLE_PARALLEL_CPU_FULL_EVALUATION,
    ENABLE_PARALLEL_CPU_MULTI_MODEL,
    MAX_CPU_UTILIZATION_MODE,
    PARALLEL_BACKTEST_WORKERS,
    PARALLEL_EVAL_WORKERS,
    PARALLEL_MULTI_MODEL_WORKERS,
    RUN_ALL_MODELS_CPU_MODE,
    STAGE2_PARALLEL_GRANULARITY,
    STAGE2_PARALLEL_MODE,
    apply_cpu_worker_limits,
    current_worker_thread_count,
    format_cpu_stage_policy_log,
    is_nested_outer_parallel,
    resolve_cpu_stage_parallel_policy,
    resolve_parallel_cpu_settings,
    resolve_stage2_parallel_policy,
)

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
DEFAULT_LOOKBACK_DAYS = 1080
TRAIN_LOOKBACK_DAYS = 1080
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
    # CatBoost has no direct dropout; use strong stochastic regularization instead.
    "l2_leaf_reg": 15,
    "bootstrap_type": "Bernoulli",
    "subsample": 0.55,
    "rsm": 0.60,
    "random_strength": 3.0,
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

# Validation-driven calibration for direction confidence/neutral deadband.
# Kept main-focused to avoid destabilizing already-strong 60min family models.
DIRECTION_CALIBRATION_ENABLED = True
DIRECTION_CALIBRATION_MAIN_ONLY = True
DIRECTION_CALIBRATION_DEADBAND_GRID = [0.0003, 0.0005, 0.0008, 0.0012]
DIRECTION_CALIBRATION_CONFIDENCE_GRID = [0.5, 0.55, 0.6, 0.65]
DIRECTION_CALIBRATION_MAX_NEUTRAL_OVERPREDICTION = 0.15
DIRECTION_CALIBRATION_MIN_UNIQUE_PREDICTED_CLASSES = 2
DIRECTION_CALIBRATION_MIN_NEUTRAL_RECALL = 0.20
# Main-only promotion gate to keep calibration aligned with near-holdout quality.
DIRECTION_CALIBRATION_MAIN_RECENT_FRACTION = 0.3
DIRECTION_CALIBRATION_MAIN_RECENT_METRIC_TOLERANCE = 0.0

# Main-only persistence alignment gate for final strategy promotion.
# Among near-tied candidates, prefer those with stronger recent edge vs persistence.
MAIN_DIRECT_PERSISTENCE_PROMOTION_ENABLED = True
MAIN_DIRECT_PERSISTENCE_PROMOTION_RECENT_FRACTION = 0.3
MAIN_DIRECT_PERSISTENCE_PROMOTION_MIN_DELTA_VS_PERSISTENCE = 0.0005
MAIN_DIRECT_PERSISTENCE_PROMOTION_MIN_RECENT_DELTA_VS_PERSISTENCE = 0.0005
MAIN_DIRECT_PERSISTENCE_PROMOTION_MAE_TOLERANCE_RATIO = 0.08

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
# When strict trigger is disabled, fallback is applied only if holdout
# underperformance vs persistence is at least this relative threshold.
MAIN_HOLDOUT_SAFEGUARD_MAX_RELATIVE_UNDERPERFORMANCE = 0.004
# If enabled, any negative final holdout delta vs persistence triggers fallback.
MAIN_HOLDOUT_SAFEGUARD_TRIGGER_ON_ANY_NEGATIVE_DELTA = False
# Prefer persistence when the final holdout edge is too small to keep risk-on
# exposure for the main pipeline.
MAIN_HOLDOUT_SAFEGUARD_PREFER_PERSISTENCE_ON_NEAR_TIE = True
MAIN_HOLDOUT_SAFEGUARD_MIN_RELATIVE_IMPROVEMENT_TO_KEEP = 0.001

# Main-only relaxed strategy selection.
# Allows selecting non-baseline candidates that are only slightly worse than
# persistence, preserving signal while leaving stronger safety checks in place.
MAIN_SELECTION_ALLOW_NEGATIVE_DELTA = True
MAIN_SELECTION_NEGATIVE_DELTA_TOLERANCE = 0.005

# Stage-1 targeted overfit stabilization for strongest signal-bearing models.
# Applies only to configured target model keys and uses previously exported
# overfit diagnostics to dampen high-variance, high-aggressiveness behavior.
OVERFIT_STABILIZATION_ENABLED = True
OVERFIT_STABILIZATION_TARGET_MODELS = ["60min_3h", "60min_6h", "60min_12h", "15min_3h", "5min_3h"]
OVERFIT_STABILIZATION_PRIMARY_MODELS = ["60min_3h", "60min_6h", "60min_12h", "15min_3h"]
OVERFIT_STABILIZATION_SIGN_GAP_MIN = 0.07

OVERFIT_STABILIZATION_PRIMARY_ALPHA_CAP_SEVERE = 0.72
OVERFIT_STABILIZATION_SECONDARY_ALPHA_CAP_SEVERE = 0.80
OVERFIT_STABILIZATION_ALPHA_CAP_MODERATE = 0.85

OVERFIT_STABILIZATION_CONFIDENCE_BUMP_SEVERE = 0.03
OVERFIT_STABILIZATION_CONFIDENCE_BUMP_MODERATE = 0.02
OVERFIT_STABILIZATION_MOVEMENT_SCALE_CAP_SEVERE = 0.96
OVERFIT_STABILIZATION_MOVEMENT_SCALE_CAP_MODERATE = 0.98
OVERFIT_STABILIZATION_EXPECTATION_DEADBAND_FLOOR_SEVERE = 0.03
OVERFIT_STABILIZATION_EXPECTATION_DEADBAND_FLOOR_MODERATE = 0.02
OVERFIT_STABILIZATION_LOW_CONFIDENCE_EXPECTATION_WEIGHT_CAP_SEVERE = 0.75
OVERFIT_STABILIZATION_LOW_CONFIDENCE_EXPECTATION_WEIGHT_CAP_MODERATE = 0.85

OVERFIT_STABILIZATION_OVERFIT_PENALTY_SCALE = 0.12
OVERFIT_STABILIZATION_OVERFIT_PENALTY_MAX = 0.35
OVERFIT_STABILIZATION_SIGN_GAP_WEIGHT = 1.0
OVERFIT_STABILIZATION_HOLDOUT_RATIO_WEIGHT = 0.8
OVERFIT_STABILIZATION_MAE_GAP_WEIGHT = 0.6
OVERFIT_STABILIZATION_SELECTION_HOLDOUT_WEIGHT = 0.62
OVERFIT_STABILIZATION_SELECTION_VALIDATION_WEIGHT = 0.38
OVERFIT_STABILIZATION_SMOOTH_HOLDOUT_RATIO_SCALE = 0.28
OVERFIT_STABILIZATION_SMOOTH_SIGN_GAP_SCALE = 0.12
OVERFIT_STABILIZATION_SMOOTH_MAE_GAP_SCALE = 0.16
OVERFIT_STABILIZATION_MODEL_ONLY_AGGRESSIVENESS_BONUS = 0.25
OVERFIT_STABILIZATION_HIGH_ALPHA_THRESHOLD = 0.70
OVERFIT_STABILIZATION_HIGH_ALPHA_AGGRESSIVENESS_BONUS = 0.15
OVERFIT_STABILIZATION_EDGE_RELIEF_FLOOR = 0.60
OVERFIT_STABILIZATION_EDGE_RELIEF_MULTIPLIER = 2.0
OVERFIT_STABILIZATION_PREDICTION_CONFIDENCE_THRESHOLD_BUFFER = 0.08
OVERFIT_STABILIZATION_PREDICTION_SIGNAL_CONFIDENCE_WEIGHT = 0.70
OVERFIT_STABILIZATION_PREDICTION_SIGNAL_EXPECTATION_WEIGHT = 0.30
OVERFIT_STABILIZATION_PREDICTION_LOW_CONFIDENCE_SHRINK_MAX = 0.24
OVERFIT_STABILIZATION_PREDICTION_DEVIATION_SOFT_LIMIT_MULTIPLIER = 1.40
OVERFIT_STABILIZATION_PREDICTION_DEVIATION_SOFT_LIMIT_FLOOR = 0.0012
OVERFIT_STABILIZATION_PREDICTION_BASELINE_MEAN_ABS_WEIGHT = 0.35

OVERFIT_STABILIZATION_PROMOTION_MAX_PENALTY_SEVERE = 0.18
OVERFIT_STABILIZATION_PROMOTION_MAX_PENALTY_MODERATE = 0.24

# Explicit target override surface for Stage-1 stabilization calibration.
# Group-level entries apply first, then exact model-key overrides are layered on
# top. This keeps target-only behavior visible and configurable.
OVERFIT_STABILIZATION_TARGET_GROUPS = {
    "60min_family": ["60min_3h", "60min_6h", "60min_12h"],
}

OVERFIT_STABILIZATION_POLICY_OVERRIDES = {
    "60min_family": {
        "activation_sign_gap_min": 0.05,
        "alpha_cap_severe": 0.68,
        "penalty_scale_multiplier": 1.25,
        "penalty_max": 0.40,
        "model_only_aggressiveness_bonus": 0.35,
        "high_alpha_threshold": 0.55,
        "high_alpha_aggressiveness_bonus": 0.22,
        "promotion_max_penalty_severe": 0.14,
        "edge_relief_floor": 0.70,
        "edge_relief_multiplier": 1.6,
        "prediction_low_confidence_shrink_max": 0.20,
        "prediction_deviation_soft_limit_multiplier": 1.55,
        "prediction_deviation_soft_limit_floor": 0.0012,
        "prediction_confidence_threshold_buffer": 0.07,
        "prediction_signal_confidence_weight": 0.65,
        "prediction_signal_expectation_weight": 0.35,
        "prediction_baseline_mean_abs_weight": 0.40,
    },
    "60min_3h": {
        "alpha_cap_severe": 0.65,
        "promotion_max_penalty_severe": 0.12,
    },
    "15min_3h": {
        "alpha_cap_severe": 0.82,
        "confidence_bump_severe": 0.015,
        "movement_scale_cap_severe": 0.98,
        "expectation_deadband_floor_severe": 0.015,
        "low_confidence_expectation_weight_cap_severe": 0.90,
        "penalty_scale_multiplier": 0.75,
        "edge_relief_floor": 0.35,
        "edge_relief_multiplier": 2.4,
        "force_expectation_to_blend_on_severe": False,
        "prefer_model_tolerance_cap": 0.0005,
        "promotion_max_penalty_severe": 0.22,
        "prediction_low_confidence_shrink_max": 0.18,
        "prediction_deviation_soft_limit_multiplier": 1.55,
        "prediction_deviation_soft_limit_floor": 0.0011,
        "prediction_confidence_threshold_buffer": 0.06,
        "prediction_signal_confidence_weight": 0.60,
        "prediction_signal_expectation_weight": 0.40,
        "prediction_baseline_mean_abs_weight": 0.45,
    },
    "5min_3h": {
        "prediction_low_confidence_shrink_max": 0.12,
        "prediction_deviation_soft_limit_multiplier": 1.70,
        "prediction_confidence_threshold_buffer": 0.05,
    },
}

# Post-screening acceleration flags. These intentionally target only the heavy
# evaluation stage after fast screening and the backtest math path. GPU is kept
# for compatibility only and is disabled by default because CPU-parallel
# evaluation is faster on the current datasets.
ENABLE_GPU_FULL_EVALUATION = False
ENABLE_GPU_BACKTEST = False
GPU_FULL_EVALUATION_DEVICE = GPU_DEVICES
GPU_BACKTEST_DEVICE = GPU_DEVICES

# Adaptive CPU execution policy. Runtime worker and CatBoost thread budgets are
# resolved centrally in core.parallel_policy from the detected logical CPU
# count instead of fixed 32-thread defaults.
RUN_ALL_MODELS_EXECUTION_MODE = RUN_ALL_MODELS_CPU_MODE
FAST_SCREENING_TARGET_THREADS = CPU_PARALLEL_TARGET_THREADS
FAST_SCREENING_OUTER_WORKERS = PARALLEL_EVAL_WORKERS
FAST_SCREENING_INNER_THREADS = CATBOOST_THREADS_PER_WORKER

STAGE2_TARGET_CPU_THREADS = CPU_PARALLEL_TARGET_THREADS
STAGE2_OUTER_WORKERS = PARALLEL_EVAL_WORKERS
STAGE2_INNER_THREADS = CATBOOST_THREADS_PER_WORKER

BACKTEST_TARGET_THREADS = CPU_PARALLEL_TARGET_THREADS
BACKTEST_OUTER_WORKERS = PARALLEL_BACKTEST_WORKERS
BACKTEST_INNER_THREADS = CPU_PARALLEL_TARGET_THREADS

BACKTEST_WINDOW_TARGET_THREADS = CPU_PARALLEL_TARGET_THREADS
BACKTEST_WINDOW_OUTER_WORKERS = PARALLEL_EVAL_WORKERS
BACKTEST_WINDOW_INNER_THREADS = CATBOOST_THREADS_PER_WORKER

MULTI_MODEL_TARGET_THREADS = CPU_PARALLEL_TARGET_THREADS
MULTI_MODEL_OUTER_WORKERS = PARALLEL_MULTI_MODEL_WORKERS
MULTI_MODEL_INNER_THREADS = CATBOOST_THREADS_PER_WORKER

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
    "l2_leaf_reg": 15,
    "bootstrap_type": "Bernoulli",
    "subsample": 0.55,
    "rsm": 0.60,
    "random_strength": 3.0,
    "loss_function": f"Quantile:alpha={RANGE_QUANTILES[0]}",
    "eval_metric": f"Quantile:alpha={RANGE_QUANTILES[0]}",
    "verbose": False,
    "random_seed": RANDOM_SEED,
}

RANGE_HIGH_CATBOOST_PARAMS = {
    "iterations": 500,
    "learning_rate": 0.05,
    "depth": 8,
    "l2_leaf_reg": 15,
    "bootstrap_type": "Bernoulli",
    "subsample": 0.55,
    "rsm": 0.60,
    "random_strength": 3.0,
    "loss_function": f"Quantile:alpha={RANGE_QUANTILES[1]}",
    "eval_metric": f"Quantile:alpha={RANGE_QUANTILES[1]}",
    "verbose": False,
    "random_seed": RANDOM_SEED,
}

DIRECT_SEARCH_GRID = {
    "depth": [100],
    "learning_rate": [0.01],
    "l2_leaf_reg": [15],
    "iterations": [12000],
    "border_count": [2500],
}

RANGE_SEARCH_GRID = {
    "depth": [100],
    "learning_rate": [0.01],
    "l2_leaf_reg": [15],
    "iterations": [12000],
    "border_count": [2500],
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
