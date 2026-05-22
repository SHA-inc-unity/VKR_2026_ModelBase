"""Тесты для backend.dataset.timelog.perf_stage + _validate_features (векторизованная версия)."""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd
import pytest

from backend.dataset.timelog import perf_stage, tlog
from backend.model.loader import _validate_features


# ---------- perf_stage ----------

def _attach_captor() -> list[logging.LogRecord]:
    records: list[logging.LogRecord] = []

    class _Captor(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    h = _Captor(level=logging.DEBUG)
    tlog.addHandler(h)
    return records


def test_perf_stage_emits_start_and_done():
    records = _attach_captor()
    with perf_stage("test.op", table="x"):
        time.sleep(0.005)
    msgs = [r.getMessage() for r in records if "test.op" in r.getMessage()]
    assert any("START" in m and "table=x" in m for m in msgs)
    assert any("DONE" in m and "elapsed=" in m for m in msgs)


def test_perf_stage_logs_failure_and_reraises():
    records = _attach_captor()
    with pytest.raises(ValueError):
        with perf_stage("test.fail"):
            raise ValueError("boom")
    msgs = [r.getMessage() for r in records if "test.fail" in r.getMessage()]
    assert any("FAILED" in m and "ValueError" in m for m in msgs)


def test_perf_stage_extra_metrics_in_done_line():
    records = _attach_captor()
    with perf_stage("test.metrics") as ctx:
        ctx["rows"] = 42
    done = [r.getMessage() for r in records if "test.metrics" in r.getMessage() and "DONE" in r.getMessage()]
    assert any("rows=42" in m for m in done)


# ---------- _validate_features (vectorized) ----------

def test_validate_features_drops_all_nan_and_constant():
    df = pd.DataFrame({
        "good": np.linspace(0, 1, 100),
        "const": np.ones(100),
        "all_nan": [np.nan] * 100,
        "ts_ignored": range(100),
    })
    kept = _validate_features(df, ["good", "const", "all_nan"])
    assert kept == ["good"]


def test_validate_features_keeps_high_nan_col():
    rng = np.random.default_rng(0)
    values = rng.normal(size=100)
    values[:40] = np.nan  # 40% NaN → warning, но колонка остаётся
    df = pd.DataFrame({
        "a": np.linspace(0, 1, 100),
        "b_high_nan": values,
    })
    kept = _validate_features(df, ["a", "b_high_nan"])
    assert set(kept) == {"a", "b_high_nan"}


def test_validate_features_empty_input():
    empty = pd.DataFrame(columns=["x"])
    assert _validate_features(empty, ["x"]) == ["x"]
    assert _validate_features(pd.DataFrame({"x": [1]}), []) == []


def test_validate_features_parity_with_reference():
    """Вект. реализация должна давать тот же результат, что и Python-цикл."""
    rng = np.random.default_rng(42)
    df = pd.DataFrame({
        "feat_a": rng.normal(size=500),
        "feat_b_const": np.full(500, 3.14),
        "feat_c_nan": [np.nan] * 500,
        "feat_d_half": np.concatenate([rng.normal(size=250), [np.nan] * 250]),
    })
    cols = ["feat_a", "feat_b_const", "feat_c_nan", "feat_d_half"]
    kept_new = _validate_features(df, cols)

    # Эквивалентная референс-реализация
    ref: list[str] = []
    for col in cols:
        s = df[col]
        nan_frac = float(s.isna().mean())
        if nan_frac >= 1.0:
            continue
        std = float(s.std(skipna=True))
        if pd.isna(std) or std == 0.0:
            continue
        ref.append(col)

    assert sorted(kept_new) == sorted(ref)
