"""Базовые примитивы обучения CatBoost: CV-splitter, Pool, конфиг модели, CPU-runtime.

Этот модуль содержит низкоуровневые помощники, разделяемые между поиском
гиперпараметров (train_search) и финальным обучением/диагностикой (train_eval).
Вынесен отдельно, чтобы избежать циклических импортов.
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd

try:
    import catboost as cb
except ImportError as exc:
    raise ImportError("Установи catboost: pip install catboost") from exc

from sklearn.model_selection import TimeSeriesSplit

from .config import (
    CV_SPLITS,
    DEVICES,
    EARLY_STOPPING_ROUNDS,
    GPU_RAM_PART,
    RANDOM_SEED,
    TASK_TYPE,
    TRAIN_FRACTION,
)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

_CPU_THREAD_LIMIT_ENV_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "OMP_THREAD_LIMIT",
    "MKL_NUM_THREADS",
    "TBB_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "GOTO_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
)

_CPU_DYNAMIC_ENV_VARS: dict[str, str] = {
    "OMP_DYNAMIC": "FALSE",
    "MKL_DYNAMIC": "FALSE",
}


def walk_forward_split(n: int, train_fraction: float = TRAIN_FRACTION) -> tuple[int, int]:
    """Возвращает (train_size, test_size) для временно́го разбиения без перемешивания."""
    train_size = int(n * train_fraction)
    return train_size, n - train_size


_CV_MODES: frozenset[str] = frozenset({"expanding", "rolling"})


def _build_cv_splitter(
    cv_mode: str,
    max_train_size: int | None,
    target_horizon_bars: int,
    n_splits: int = CV_SPLITS,
) -> TimeSeriesSplit:
    """Возвращает настроенный TimeSeriesSplit для выбранного режима.

    cv_mode:
        "expanding" (по умолчанию) — каждый фолд добавляет данные слева, train-окно растёт.
        "rolling"                 — train-окно фиксированного размера max_train_size
                                    (если None, sklearn сам определяет разумный предел;
                                    см. TimeSeriesSplit.max_train_size).

    target_horizon_bars — gap между train/val (purge gap против утечки).
    """
    if cv_mode not in _CV_MODES:
        raise ValueError(
            f"cv_mode должен быть одним из {sorted(_CV_MODES)}, получено {cv_mode!r}"
        )
    mts = max_train_size if cv_mode == "rolling" else None
    if mts is not None and mts <= 0:
        mts = None
    return TimeSeriesSplit(n_splits=n_splits, gap=target_horizon_bars, max_train_size=mts)


def _build_pool(
    X: pd.DataFrame,
    y: pd.Series | None = None,
) -> "cb.Pool":
    """Создаёт CatBoost Pool из DataFrame (NaN сохраняются как missing values)."""
    return cb.Pool(
        data=X.values,
        label=y.values if y is not None else None,
        feature_names=list(X.columns),
    )


def _prepare_model_params(params: dict[str, Any], *, use_gpu: bool) -> dict[str, Any]:
    """Подготавливает параметры CatBoost под выбранное устройство.

    border_count (число бинов квантования) поддерживается и на CPU, и на GPU —
    удалять его нельзя: без него CatBoost CPU использует дефолтное значение 254,
    что при depth=10 удваивает рабочий набор гистограмм и гарантирует
    memory-bandwidth bottleneck даже для комбо с border_count=128 из сетки.
    """
    result = dict(params)
    if not use_gpu:
        result.pop("gpu_ram_part", None)
        result.pop("devices", None)
    return result


def _get_full_cpu_thread_count() -> int:
    """Возвращает число потоков для загрузки всех доступных CPU."""
    try:
        return max(len(os.sched_getaffinity(0)), 1)
    except (AttributeError, OSError):
        return max(os.cpu_count() or 1, 1)


def _configure_full_cpu_runtime(thread_count: int) -> None:
    """Снимает внешние лимиты потоков и выставляет полную загрузку CPU."""
    for env_name in _CPU_THREAD_LIMIT_ENV_VARS:
        os.environ[env_name] = str(thread_count)
    for env_name, env_value in _CPU_DYNAMIC_ENV_VARS.items():
        os.environ[env_name] = env_value


def _make_model(
    params: dict[str, Any],
    *,
    verbose: int = 0,
    use_gpu: bool = True,
    early_stopping_rounds: int = EARLY_STOPPING_ROUNDS,
) -> "cb.CatBoostRegressor":
    """Собирает CatBoostRegressor с нужными параметрами."""
    p = _prepare_model_params(params, use_gpu=use_gpu)

    model_params: dict[str, Any] = {
        **p,
        "loss_function":        "RMSE",
        "eval_metric":          "RMSE",
        "random_seed":          RANDOM_SEED,
        "early_stopping_rounds": early_stopping_rounds,
        "verbose":              verbose,
    }
    if use_gpu:
        model_params["task_type"]    = TASK_TYPE
        model_params["devices"]      = DEVICES
        model_params["gpu_ram_part"] = GPU_RAM_PART
    else:
        # _configure_full_cpu_runtime вызывается один раз перед циклом в
        # grid_search_cv / train_final_model, а не на каждый fold.
        cpu_threads = _get_full_cpu_thread_count()
        model_params["task_type"]   = "CPU"
        model_params["thread_count"] = cpu_threads
    return cb.CatBoostRegressor(**model_params)
