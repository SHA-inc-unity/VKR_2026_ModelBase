"""Пакет обучения и оценки CatBoost-модели для прогнозирования target_return_1."""
from __future__ import annotations

from .config import (
    DEFAULT_PARAM_VALUES,
    DEVICES,
    EARLY_STOPPING_ROUNDS,
    GPU_RAM_PART,
    MODELS_DIR,
    PARAM_GRID,
    RANDOM_SEED,
    TARGET_COLUMN,
    TASK_TYPE,
    TRAIN_FRACTION,
    VERBOSE_TRAIN,
    expand_param_grid,
)
from .loader import load_training_data
from .metrics import compute_direction_metrics, compute_metrics, compute_trading_metrics
from .report import (
    load_grid_best_params,
    plot_actual_vs_predicted,
    plot_cumulative_pnl,
    plot_feature_importance,
    save_grid_best_params,
    save_grid_results,
    save_predictions_json,
    save_results_json,
)
from .train import compute_overfitting_diagnostics, grid_search_cv, save_model, train_final_model, walk_forward_split

__all__ = [
    "DEFAULT_PARAM_VALUES",
    "DEVICES",
    "EARLY_STOPPING_ROUNDS",
    "GPU_RAM_PART",
    "MODELS_DIR",
    "PARAM_GRID",
    "RANDOM_SEED",
    "TARGET_COLUMN",
    "TASK_TYPE",
    "TRAIN_FRACTION",
    "VERBOSE_TRAIN",
    "expand_param_grid",
    "load_training_data",
    "compute_metrics",
    "compute_direction_metrics",
    "compute_trading_metrics",
    "load_grid_best_params",
    "plot_actual_vs_predicted",
    "plot_cumulative_pnl",
    "plot_feature_importance",
    "save_grid_best_params",
    "save_grid_results",
    "save_predictions_json",
    "save_results_json",
    "compute_overfitting_diagnostics",
    "grid_search_cv",
    "save_model",
    "train_final_model",
    "walk_forward_split",
]
