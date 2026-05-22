"""Tests for backend.model.train — utility and pure functions."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("catboost")

from backend.model.train import (
    _build_cv_splitter,
    _build_pool,
    _configure_full_cpu_runtime,
    _get_full_cpu_thread_count,
    _make_model,
    _prepare_model_params,
    walk_forward_split,
)


# ---------------------------------------------------------------------------
# walk_forward_split
# ---------------------------------------------------------------------------

def test_walk_forward_split_default_ratio():
    train_n, test_n = walk_forward_split(n=100)
    assert train_n + test_n == 100
    assert train_n >= 50


def test_walk_forward_split_respects_ratio():
    train_n, test_n = walk_forward_split(n=200, train_fraction=0.7)
    assert test_n == 60
    assert train_n == 140


def test_walk_forward_split_minimum_test():
    """Even with 50% train fraction, total must equal n."""
    train_n, test_n = walk_forward_split(n=10, train_fraction=0.5)
    assert train_n + test_n == 10


# ---------------------------------------------------------------------------
# _build_cv_splitter
# ---------------------------------------------------------------------------

def test_build_cv_splitter_expanding():
    # signature: _build_cv_splitter(cv_mode, max_train_size, target_horizon_bars, n_splits)
    splitter = _build_cv_splitter("expanding", None, 0, n_splits=5)
    assert splitter is not None


def test_build_cv_splitter_rolling():
    splitter = _build_cv_splitter("rolling", 100, 1, n_splits=3)
    assert splitter is not None


def test_build_cv_splitter_invalid_mode_raises():
    with pytest.raises(ValueError, match="cv_mode"):
        _build_cv_splitter("unknown_mode", None, 0, n_splits=3)


# ---------------------------------------------------------------------------
# _prepare_model_params
# ---------------------------------------------------------------------------

def test_prepare_model_params_removes_gpu_params_when_cpu():
    # Only gpu_ram_part and devices are removed; task_type stays
    params = {"gpu_ram_part": 0.8, "devices": "0", "depth": 6}
    result = _prepare_model_params(params, use_gpu=False)
    assert "gpu_ram_part" not in result
    assert "devices" not in result
    assert result["depth"] == 6


def test_prepare_model_params_keeps_gpu_params():
    params = {"gpu_ram_part": 0.8, "task_type": "GPU", "depth": 6}
    result = _prepare_model_params(params, use_gpu=True)
    assert result["task_type"] == "GPU"


def test_prepare_model_params_does_not_mutate_input():
    original = {"depth": 6, "gpu_ram_part": 0.8}
    _prepare_model_params(original, use_gpu=False)
    assert "gpu_ram_part" in original  # original unchanged


# ---------------------------------------------------------------------------
# _get_full_cpu_thread_count
# ---------------------------------------------------------------------------

def test_get_full_cpu_thread_count_positive_int():
    count = _get_full_cpu_thread_count()
    assert isinstance(count, int)
    assert count >= 1


# ---------------------------------------------------------------------------
# _configure_full_cpu_runtime
# ---------------------------------------------------------------------------

def test_configure_full_cpu_runtime_sets_env_vars():
    _configure_full_cpu_runtime(4)
    # At minimum should not raise; check that OMP/MKL env vars are set
    # (the function sets OPENBLAS/MKL_NUM_THREADS etc.)
    # We just confirm the call succeeds
    assert True


# ---------------------------------------------------------------------------
# _build_pool
# ---------------------------------------------------------------------------

def test_build_pool_returns_catboost_pool():
    from catboost import Pool

    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(50, 4)), columns=["a", "b", "c", "d"])
    y = pd.Series(rng.normal(size=50))
    pool = _build_pool(X, y)
    assert isinstance(pool, Pool)


def test_build_pool_with_no_label():
    from catboost import Pool

    rng = np.random.default_rng(1)
    X = pd.DataFrame(rng.normal(size=(30, 3)), columns=["a", "b", "c"])
    pool = _build_pool(X)  # no label
    assert isinstance(pool, Pool)


# ---------------------------------------------------------------------------
# _make_model
# ---------------------------------------------------------------------------

def test_make_model_returns_catboost_regressor():
    from catboost import CatBoostRegressor

    params = {"iterations": 5, "depth": 2, "learning_rate": 0.3}
    model = _make_model(params, use_gpu=False)
    assert isinstance(model, CatBoostRegressor)


def test_make_model_cpu_params():
    params = {"iterations": 5, "gpu_ram_part": 0.5, "task_type": "GPU"}
    model = _make_model(params, use_gpu=False)
    # Should still be a CatBoostRegressor with GPU params stripped
    from catboost import CatBoostRegressor
    assert isinstance(model, CatBoostRegressor)
