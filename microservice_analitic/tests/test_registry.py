"""Tests for backend.model.report registry functions:
register_model_version, load_registry, delete_registry_version.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from backend.model.report import (
    delete_registry_version,
    load_registry,
    register_model_version,
)

_METRICS = {"sharpe": 1.8, "RMSE": 0.0004, "dir_acc_pct": 55.2, "profit_factor": 1.3}
_PARAMS  = {"depth": 6, "learning_rate": 0.03, "iterations": 2000}
_FEATS   = [f"feat_{i}" for i in range(15)]


@pytest.fixture()
def tmpdir() -> pathlib.Path:
    with tempfile.TemporaryDirectory() as d:
        yield pathlib.Path(d)


# ---------------------------------------------------------------------------
# register_model_version
# ---------------------------------------------------------------------------

def test_register_creates_file(tmpdir):
    vid = register_model_version(
        "catboost_btcusdt_60m", _METRICS, _PARAMS, _FEATS,
        models_dir=tmpdir, n_train=1000, n_test=300,
    )
    reg_path = tmpdir / "registry.json"
    assert reg_path.exists()
    assert vid.startswith("catboost_btcusdt_60m_")


def test_register_roundtrip_fields(tmpdir):
    vid = register_model_version(
        "catboost_btcusdt_60m", _METRICS, _PARAMS, _FEATS,
        models_dir=tmpdir,
        mlflow_run_id="abc123",
        target_col="target_return_1",
        n_train=800, n_test=200,
    )
    entries = load_registry(models_dir=tmpdir)
    assert len(entries) == 1
    e = entries[0]
    assert e["version_id"] == vid
    assert e["prefix"] == "catboost_btcusdt_60m"
    assert e["mlflow_run_id"] == "abc123"
    assert e["target_col"] == "target_return_1"
    assert e["n_train"] == 800
    assert e["n_test"] == 200
    assert e["n_features"] == len(_FEATS)
    assert abs(e["metrics"]["sharpe"] - _METRICS["sharpe"]) < 1e-9


def test_register_multiple_entries_newest_first(tmpdir):
    v1 = register_model_version("pfx_a", _METRICS, _PARAMS, _FEATS, models_dir=tmpdir)
    v2 = register_model_version("pfx_b", _METRICS, _PARAMS, _FEATS, models_dir=tmpdir)
    entries = load_registry(models_dir=tmpdir)
    # newest (v2) должна быть первой
    assert entries[0]["version_id"] == v2
    assert entries[1]["version_id"] == v1


# ---------------------------------------------------------------------------
# load_registry
# ---------------------------------------------------------------------------

def test_load_registry_empty(tmpdir):
    assert load_registry(models_dir=tmpdir) == []


def test_load_registry_prefix_filter(tmpdir):
    register_model_version("catboost_btcusdt_60m", _METRICS, _PARAMS, _FEATS, models_dir=tmpdir)
    register_model_version("catboost_ethusdt_60m", _METRICS, _PARAMS, _FEATS, models_dir=tmpdir)
    filtered = load_registry(models_dir=tmpdir, prefix_filter="catboost_btcusdt_60m")
    assert len(filtered) == 1
    assert filtered[0]["prefix"] == "catboost_btcusdt_60m"


def test_load_registry_limit(tmpdir):
    for i in range(5):
        register_model_version(f"pfx_{i}", _METRICS, _PARAMS, _FEATS, models_dir=tmpdir)
    assert len(load_registry(models_dir=tmpdir, limit=3)) == 3


def test_load_registry_no_mlflow_run_id(tmpdir):
    register_model_version("pfx", _METRICS, _PARAMS, _FEATS, models_dir=tmpdir)
    e = load_registry(models_dir=tmpdir)[0]
    assert e["mlflow_run_id"] is None


# ---------------------------------------------------------------------------
# delete_registry_version
# ---------------------------------------------------------------------------

def test_delete_existing(tmpdir):
    vid = register_model_version("pfx", _METRICS, _PARAMS, _FEATS, models_dir=tmpdir)
    assert delete_registry_version(vid, models_dir=tmpdir) is True
    assert load_registry(models_dir=tmpdir) == []


def test_delete_nonexistent(tmpdir):
    register_model_version("pfx", _METRICS, _PARAMS, _FEATS, models_dir=tmpdir)
    assert delete_registry_version("does_not_exist_xyz", models_dir=tmpdir) is False
    assert len(load_registry(models_dir=tmpdir)) == 1


def test_delete_one_of_many(tmpdir):
    import time as _time
    v1 = register_model_version("pfx_a", _METRICS, _PARAMS, _FEATS, models_dir=tmpdir)
    _time.sleep(1.1)  # ensure different second → different version_id
    v2 = register_model_version("pfx_b", _METRICS, _PARAMS, _FEATS, models_dir=tmpdir)
    assert v1 != v2, "version_ids должны различаться"
    delete_registry_version(v1, models_dir=tmpdir)
    remaining = load_registry(models_dir=tmpdir)
    assert len(remaining) == 1
    assert remaining[0]["version_id"] == v2


def test_delete_missing_file_returns_false(tmpdir):
    assert delete_registry_version("any_id", models_dir=tmpdir) is False


# ---------------------------------------------------------------------------
# metrics stored as numeric-only
# ---------------------------------------------------------------------------

def test_register_filters_non_numeric_metrics(tmpdir):
    metrics_mixed = {**_METRICS, "label": "good", "nested": {"a": 1}}
    register_model_version("pfx", metrics_mixed, _PARAMS, _FEATS, models_dir=tmpdir)
    e = load_registry(models_dir=tmpdir)[0]
    assert "label" not in e["metrics"]
    assert "nested" not in e["metrics"]
    assert "sharpe" in e["metrics"]
