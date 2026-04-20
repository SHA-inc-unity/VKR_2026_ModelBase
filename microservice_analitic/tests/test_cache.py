"""Tests for backend.model.cache: round-trip, TTL, clear, stats."""
from __future__ import annotations

import time

import pandas as pd
import pytest

pytest.importorskip("pyarrow")

from backend.model.cache import (
    _cache_key,
    cache_stats,
    clear_cache,
    load_cached_dataset,
    save_cached_dataset,
)


def _make_dataset(n: int = 50) -> tuple[pd.DataFrame, pd.Series, list[str], pd.Series]:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    X = pd.DataFrame({
        "feat_a": range(n),
        "feat_b": [float(i) * 0.5 for i in range(n)],
    })
    y = pd.Series([float(i) * 0.01 for i in range(n)], name="target_return_1")
    feature_cols = ["feat_a", "feat_b"]
    ts = pd.Series(idx, name="timestamp_utc")
    return X, y, feature_cols, ts


def test_cache_key_is_stable_and_distinct():
    k1 = _cache_key("tbl_a", "2024-01-01", "2024-02-01", "target_return_1")
    k2 = _cache_key("tbl_a", "2024-01-01", "2024-02-01", "target_return_1")
    k3 = _cache_key("tbl_a", "2024-01-01", "2024-02-01", "target_return_5")
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 16


def test_save_and_load_roundtrip(tmp_path):
    X, y, cols, ts = _make_dataset()
    save_cached_dataset(
        X, y, cols, ts,
        table_name="bars_btcusdt_1h",
        date_from="2024-01-01", date_to="2024-02-01",
        target_col="target_return_1",
        cache_dir=tmp_path,
    )
    hit = load_cached_dataset(
        "bars_btcusdt_1h",
        date_from="2024-01-01", date_to="2024-02-01",
        target_col="target_return_1",
        cache_dir=tmp_path,
    )
    assert hit is not None
    X2, y2, cols2, ts2 = hit
    assert list(cols2) == cols
    assert len(X2) == len(X)
    pd.testing.assert_frame_equal(X2.reset_index(drop=True), X.reset_index(drop=True))
    assert y2.tolist() == y.tolist()


def test_load_miss_on_wrong_key(tmp_path):
    X, y, cols, ts = _make_dataset()
    save_cached_dataset(
        X, y, cols, ts,
        table_name="bars_btcusdt_1h",
        date_from="2024-01-01", date_to="2024-02-01",
        target_col="target_return_1",
        cache_dir=tmp_path,
    )
    assert load_cached_dataset(
        "bars_btcusdt_1h",
        date_from="2024-01-01", date_to="2024-02-01",
        target_col="target_return_5",
        cache_dir=tmp_path,
    ) is None


def test_ttl_expiry(tmp_path):
    X, y, cols, ts = _make_dataset()
    save_cached_dataset(
        X, y, cols, ts,
        table_name="bars_btcusdt_1h",
        target_col="target_return_1",
        cache_dir=tmp_path,
    )
    time.sleep(0.05)
    assert load_cached_dataset(
        "bars_btcusdt_1h", target_col="target_return_1",
        cache_dir=tmp_path, max_age_s=0.01,
    ) is None
    assert load_cached_dataset(
        "bars_btcusdt_1h", target_col="target_return_1",
        cache_dir=tmp_path, max_age_s=3600,
    ) is not None


def test_clear_and_stats(tmp_path):
    X, y, cols, ts = _make_dataset()
    save_cached_dataset(
        X, y, cols, ts, table_name="t1",
        target_col="target_return_1", cache_dir=tmp_path,
    )
    save_cached_dataset(
        X, y, cols, ts, table_name="t2",
        target_col="target_return_1", cache_dir=tmp_path,
    )
    stats = cache_stats(tmp_path)
    assert stats["n_files"] == 2
    assert stats["total_bytes"] > 0
    tables = {e["table"] for e in stats["entries"]}
    assert tables == {"t1", "t2"}
    n_deleted = clear_cache(tmp_path)
    assert n_deleted == 4
    assert cache_stats(tmp_path)["n_files"] == 0
