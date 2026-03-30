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
