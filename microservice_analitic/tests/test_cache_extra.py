"""Extra tests for backend.model.cache — edge cases not covered by test_cache.py."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.model.cache import (
    _cache_key,
    _paths,
    cache_stats,
    load_cached_dataset,
    save_cached_dataset,
)


def _sample_data():
    rng = np.random.default_rng(42)
    n = 100
    X = pd.DataFrame(rng.normal(size=(n, 3)), columns=["a", "b", "c"])
    y = pd.Series(rng.normal(size=n), name="target_return_1")
    ts = pd.Series(pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"))
    feature_cols = ["a", "b", "c"]
    target_col = "target_return_1"
    return X, y, ts, feature_cols, target_col


def _save(tmp_path, target_col="target_return_1"):
    X, y, ts, feature_cols, tc = _sample_data()
    save_cached_dataset(
        X, y, feature_cols, ts,
        table_name="btcusdt_60m",
        target_col=target_col,
        cache_dir=tmp_path,
    )
    return X, y, ts, feature_cols, tc


def _key(table="btcusdt_60m", target_col="target_return_1"):
    return _cache_key(table, None, None, target_col)


# ---------------------------------------------------------------------------
# _paths (private utility)
# ---------------------------------------------------------------------------

def test_paths_returns_parquet_and_meta(tmp_path):
    key = _key()
    pq_path, meta_path = _paths(key, tmp_path)
    assert pq_path.suffix == ".parquet"
    assert meta_path.suffix == ".json"


# ---------------------------------------------------------------------------
# cache_stats
# ---------------------------------------------------------------------------

def test_cache_stats_no_cache_dir(tmp_path):
    missing = tmp_path / "nonexistent"
    stats = cache_stats(cache_dir=missing)
    assert stats["n_files"] == 0


def test_cache_stats_with_entries(tmp_path):
    _save(tmp_path)
    stats = cache_stats(cache_dir=tmp_path)
    assert stats["n_files"] > 0
    assert stats["total_bytes"] > 0


# ---------------------------------------------------------------------------
# TTL / expired cache
# ---------------------------------------------------------------------------

def test_load_returns_none_when_ttl_expired(tmp_path):
    _save(tmp_path)
    # max_age_s=0 → any cache is expired
    result = load_cached_dataset("btcusdt_60m", target_col="target_return_1",
                                 cache_dir=tmp_path, max_age_s=0)
    assert result is None


# ---------------------------------------------------------------------------
# Corrupt parquet file
# ---------------------------------------------------------------------------

def test_load_returns_none_on_corrupt_parquet(tmp_path):
    _save(tmp_path)
    key = _key()
    pq_path, _ = _paths(key, tmp_path)
    pq_path.write_bytes(b"NOTAPARQUETFILE")
    result = load_cached_dataset("btcusdt_60m", target_col="target_return_1",
                                 cache_dir=tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# Corrupt meta.json
# ---------------------------------------------------------------------------

def test_load_returns_none_on_corrupt_meta(tmp_path):
    _save(tmp_path)
    key = _key()
    _, meta_path = _paths(key, tmp_path)
    meta_path.write_text("NOT VALID JSON")
    result = load_cached_dataset("btcusdt_60m", target_col="target_return_1",
                                 cache_dir=tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# Missing target column in parquet
# ---------------------------------------------------------------------------

def test_load_returns_none_when_target_col_missing_in_parquet(tmp_path):
    _save(tmp_path)
    # Reload with a different target_col that was not saved → parquet won't have it
    result = load_cached_dataset("btcusdt_60m", target_col="target_return_99",
                                 cache_dir=tmp_path)
    # Different key → cache miss (None)
    assert result is None


def test_load_returns_none_when_target_col_not_in_df(tmp_path):
    """Write a parquet manually that lacks the target_col but has correct key."""
    X, y, ts, feature_cols, target_col = _sample_data()
    # Save normally, then overwrite parquet without target column
    save_cached_dataset(X, y, feature_cols, ts,
                        table_name="btcusdt_60m", target_col=target_col,
                        cache_dir=tmp_path)
    key = _key()
    pq_path, _ = _paths(key, tmp_path)
    # Write parquet without target_return_1 column
    bad_df = pd.DataFrame({"timestamp_utc": ts.values, "a": X["a"].values})
    bad_df.to_parquet(pq_path, index=False)
    result = load_cached_dataset("btcusdt_60m", target_col=target_col, cache_dir=tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# Missing feature columns in parquet
# ---------------------------------------------------------------------------

def test_load_returns_none_when_feature_cols_missing_in_df(tmp_path):
    """Meta says features=[a,b,c,missing], parquet only has [a,b,c] → miss."""
    X, y, ts, feature_cols, target_col = _sample_data()
    # Save with feature_cols that include a non-existent column in the meta
    save_cached_dataset(X, y, feature_cols + ["missing_feat"], ts,
                        table_name="btcusdt_60m", target_col=target_col,
                        cache_dir=tmp_path)
    result = load_cached_dataset("btcusdt_60m", target_col=target_col, cache_dir=tmp_path)
    assert result is None
