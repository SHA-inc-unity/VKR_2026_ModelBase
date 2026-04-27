"""Tests for backend.dataset.features — feature engineering pipeline."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.dataset.features import (
    _infer_step_ms,
    build_features,
    get_feature_columns,
    prepare_for_catboost,
)


def _minimal_df(n: int = 50, timeframe: str = "60m") -> pd.DataFrame:
    """Creates a minimal market DataFrame for testing."""
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    prices = 40_000.0 + np.arange(n, dtype=float) * 10.0
    return pd.DataFrame({
        "timestamp_utc": ts,
        "close_price": prices,
        "symbol": "BTCUSDT",
        "timeframe": timeframe,
        "funding_rate": [0.0001] * n,
        "open_interest": [100.0 + i for i in range(n)],
        "rsi": [50.0] * n,
    })


# ---------------------------------------------------------------------------
# _infer_step_ms
# ---------------------------------------------------------------------------

def test_infer_step_ms_from_timeframe_column():
    df = _minimal_df()
    step = _infer_step_ms(df)
    assert step == 3_600_000  # 60m in ms


def test_infer_step_ms_fallback_from_diff():
    df = _minimal_df()
    df = df.drop(columns=["timeframe"])
    step = _infer_step_ms(df)
    assert step == 3_600_000  # derived from timestamp diff


def test_infer_step_ms_single_row_defaults():
    df = _minimal_df(n=1)
    df = df.drop(columns=["timeframe"])
    step = _infer_step_ms(df)
    assert step == 3_600_000  # default 1h


# ---------------------------------------------------------------------------
# build_features
# ---------------------------------------------------------------------------

def test_build_features_returns_dataframe():
    df = _minimal_df(n=80)
    result = build_features(df, warmup_candles=0)
    assert isinstance(result, pd.DataFrame)
    assert len(result) > 0


def test_build_features_missing_required_column_raises():
    df = _minimal_df(n=80)
    df = df.drop(columns=["close_price"])
    with pytest.raises(ValueError, match="обязательные"):
        build_features(df)


def test_build_features_warmup_trims_rows():
    df = _minimal_df(n=80)
    result_no_warmup = build_features(df, warmup_candles=0)
    result_warmup = build_features(df, warmup_candles=5)
    assert len(result_warmup) == len(result_no_warmup) - 5


def test_build_features_without_groupby():
    df = _minimal_df(n=80)
    df = df.drop(columns=["symbol", "timeframe"])
    result = build_features(df, warmup_candles=0)
    assert "close_price" in result.columns


def test_build_features_no_target():
    df = _minimal_df(n=80)
    result = build_features(df, add_target=False, warmup_candles=0)
    assert "target_return_1" not in result.columns


def test_build_features_with_target():
    df = _minimal_df(n=80)
    result = build_features(df, add_target=True, warmup_candles=0)
    assert "target_return_1" in result.columns


def test_build_features_multiple_groups():
    df1 = _minimal_df(n=60, timeframe="60m")
    df2 = _minimal_df(n=60, timeframe="5m")
    df2["timeframe"] = "5m"
    combined = pd.concat([df1, df2], ignore_index=True)
    result = build_features(combined, warmup_candles=0)
    assert len(result) > 0
    # Both timeframes should appear in the result
    assert set(result["timeframe"].unique()) == {"60m", "5m"}


def test_build_features_does_not_mutate_input():
    """Регрессия на перф-оптимизацию: build_features убрал внутренний df.copy()
    ради экономии ~1.26 ГБ на 3M-строчных датасетах. Контракт функции требует,
    чтобы входной DataFrame оставался неизменным (ни columns, ни длина, ни
    значения колонок)."""
    df = _minimal_df(n=80)
    orig_cols = list(df.columns)
    orig_len = len(df)
    orig_prices = df["close_price"].to_numpy().copy()
    _ = build_features(df, add_target=True, warmup_candles=0)
    assert list(df.columns) == orig_cols, "build_features добавил колонки во входной df"
    assert len(df) == orig_len, "build_features изменил длину входного df"
    np.testing.assert_array_equal(df["close_price"].to_numpy(), orig_prices)


def test_build_features_single_group_fastpath_parity():
    """Single-group fast-path (пропуск финального sort_values + пропуск concat)
    должен давать идентичный результат общему пути (multi-group + concat)."""
    df = _minimal_df(n=80)
    # Фактически single-group (один symbol+timeframe) — идёт по fast-path
    res_single = build_features(df.copy(), warmup_candles=0).reset_index(drop=True)
    # Форсируем multi-group путь: делим на два таймфрейма с перемешиванием
    df_a = df.head(40).copy()
    df_b = df.tail(40).copy()
    df_b["timeframe"] = "5m"
    combined = pd.concat([df_a, df_b], ignore_index=True)
    res_multi = build_features(combined, warmup_candles=0)
    # Берём из multi-group результата только строки первого таймфрейма и сверяем
    # базовые столбцы: они должны совпадать с fast-path расчётом.
    res_multi_a = res_multi[res_multi["timeframe"] == "60m"].reset_index(drop=True)
    # Первые 40 строк single-path датасета соответствуют df_a
    assert len(res_multi_a) == 40
    expected = res_single.head(40).reset_index(drop=True)
    pd.testing.assert_series_equal(
        res_multi_a["close_price"].reset_index(drop=True),
        expected["close_price"].reset_index(drop=True),
        check_names=False,
    )


# ---------------------------------------------------------------------------
# get_feature_columns
# ---------------------------------------------------------------------------

def test_get_feature_columns_excludes_raw_cols():
    df = _minimal_df(n=80)
    result = build_features(df, warmup_candles=0)
    feature_cols = get_feature_columns(result)
    # Raw columns like close_price, timestamp_utc should not appear
    assert "close_price" not in feature_cols
    assert "timestamp_utc" not in feature_cols
    assert len(feature_cols) > 0


# ---------------------------------------------------------------------------
# prepare_for_catboost
# ---------------------------------------------------------------------------

def test_prepare_for_catboost_returns_x_and_y():
    df = _minimal_df(n=80)
    result = build_features(df, warmup_candles=0)
    X, y = prepare_for_catboost(result)
    assert isinstance(X, pd.DataFrame)
    assert isinstance(y, pd.Series)
    assert len(X) == len(y)


def test_prepare_for_catboost_drops_na_target():
    df = _minimal_df(n=80)
    result = build_features(df, warmup_candles=0)
    X, y = prepare_for_catboost(result, drop_na_target=True)
    assert y.isna().sum() == 0


def test_prepare_for_catboost_no_feature_cols_raises():
    from backend.dataset.constants import RAW_FEATURE_COLUMNS
    df = pd.DataFrame({"timestamp_utc": pd.date_range("2024-01-01", periods=5, freq="1h")})
    with pytest.raises(ValueError, match="Столбцы признаков не найдены"):
        prepare_for_catboost(df)


def test_prepare_for_catboost_no_target_column():
    df = _minimal_df(n=80)
    result = build_features(df, add_target=False, warmup_candles=0)
    X, y = prepare_for_catboost(result)
    assert y is None
