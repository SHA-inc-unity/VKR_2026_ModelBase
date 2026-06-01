"""Обучение CatBoost: walk-forward split, grid search, финальное обучение.

Фасад: модуль исторически содержал весь код обучения. Он разбит на
cohesive-подмодули, но публичная (и приватная, используемая тестами) поверхность
импорта сохранена — все имена остаются импортируемыми из ``backend.model.train``:

    train_base   — низкоуровневые примитивы (CV-splitter, Pool, конфиг модели,
                   CPU-runtime, walk_forward_split);
    train_search — поиск гиперпараметров (grid_search_cv, optuna_search_cv);
    train_eval   — финальное обучение, сохранение модели, диагностика переобучения.
"""
from __future__ import annotations

from .train_base import (
    _CPU_DYNAMIC_ENV_VARS,
    _CPU_THREAD_LIMIT_ENV_VARS,
    _CV_MODES,
    _build_cv_splitter,
    _build_pool,
    _configure_full_cpu_runtime,
    _get_full_cpu_thread_count,
    _make_model,
    _prepare_model_params,
    walk_forward_split,
)
from .train_search import (
    grid_search_cv,
    optuna_search_cv,
)
from .train_eval import (
    compute_overfitting_diagnostics,
    save_model,
    train_final_model,
)

__all__ = [
    # train_base
    "walk_forward_split",
    "_build_cv_splitter",
    "_build_pool",
    "_prepare_model_params",
    "_get_full_cpu_thread_count",
    "_configure_full_cpu_runtime",
    "_make_model",
    "_CV_MODES",
    "_CPU_THREAD_LIMIT_ENV_VARS",
    "_CPU_DYNAMIC_ENV_VARS",
    # train_search
    "grid_search_cv",
    "optuna_search_cv",
    # train_eval
    "train_final_model",
    "save_model",
    "compute_overfitting_diagnostics",
]
