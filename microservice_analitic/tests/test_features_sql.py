"""Тесты для SQL-first feature-generator и pipeline_sql.

Проверяем структуру генерируемого SQL (без выполнения — не требуют PG):
- все 43 feature-колонки из FEATURE_TABLE_SCHEMA присутствуют в SELECT-list;
- используются правильные SQL-конструкции (LAG, STDDEV_POP — не STDDEV_SAMP,
  ROWS BETWEEN, ISODOW-1 для dayofweek, GREATEST с 1e-10 для log-return);
- ffill-CTE содержит count-grouping idiom для PG14-совместимости;
- merge-statement в pipeline_sql собирается без ошибок и содержит
  IS DISTINCT FROM (сохранение Fix A из Round 2).
"""
from __future__ import annotations

import inspect

import pytest

from backend.dataset.constants import FEATURE_TABLE_SCHEMA
from backend.dataset.features_sql import (
    FFILL_CTE_COLUMNS,
    FFILL_SELECT_COLUMNS,
    build_feature_select_clause,
    resolve_step_ms_for_timeframe,
)


class _FakeConn:
    """Минимальный фейк psycopg2-connection для as_string()."""
    def __init__(self):
        import psycopg2  # type: ignore
    # psycopg2.sql.Composed.as_string принимает conn-like объект с .encoding
    encoding = "UTF8"


def _render(clause):
    """Рендерит sql.Composed → str для проверок текста."""
    # psycopg2 ожидает curor_or_conn с get_dsn_parameters(); проще — str на .strings
    parts = []
    for c in clause.seq:
        if hasattr(c, "string"):
            parts.append(c.string)
        elif hasattr(c, "seq"):
            parts.append(_render(c))
        else:
            parts.append(str(c))
    return " ".join(parts)


def test_feature_select_covers_all_feature_schema_columns():
    fs = build_feature_select_clause(step_ms=3_600_000, add_target=True)
    expected = {name for name, _ in FEATURE_TABLE_SCHEMA}
    produced = set(fs.column_names)
    missing = expected - produced
    extra = produced - expected
    assert not missing, f"Отсутствуют feature-колонки в SQL: {missing}"
    assert not extra, f"Лишние feature-колонки в SQL: {extra}"
    # количество колонок совпадает
    assert len(fs.column_names) == len(expected)


def test_feature_select_without_target():
    fs = build_feature_select_clause(step_ms=3_600_000, add_target=False)
    assert "target_return_1" not in fs.column_names
    # всё остальное должно быть
    assert "oi_return_1" in fs.column_names
    assert "hour_sin" in fs.column_names


def test_feature_select_uses_pandas_compatible_aggregates():
    """pandas std(ddof=0) == PG STDDEV_POP (не STDDEV_SAMP)."""
    fs = build_feature_select_clause(step_ms=3_600_000)
    rendered = str(fs.select_clause)
    assert "STDDEV_POP" in rendered
    assert "STDDEV_SAMP" not in rendered
    # log_return использует GREATEST(..., 1e-10) — совпадает с np.log(clip(..., 1e-10, inf))
    assert "GREATEST" in rendered
    assert "1e-10" in rendered
    # rolling окна: ROWS BETWEEN N PRECEDING AND CURRENT ROW
    assert "PRECEDING AND CURRENT ROW" in rendered


def test_feature_select_uses_isodow_for_dayofweek():
    """pandas dt.dayofweek имеет Monday=0..Sunday=6 == PG EXTRACT(ISODOW)-1."""
    fs = build_feature_select_clause(step_ms=3_600_000)
    rendered = str(fs.select_clause)
    assert "ISODOW" in rendered


def test_feature_select_uses_nullif_for_divisions():
    """Защита от деления на ноль: NULLIF(..., 0) должен встречаться для всех
    формул, где pandas использует replace(0, NaN)."""
    fs = build_feature_select_clause(step_ms=3_600_000)
    rendered = str(fs.select_clause)
    # price_to_roll, price_vol, return_*, oi_to_funding
    assert rendered.count("NULLIF") >= 10


def test_ffill_cte_uses_count_based_grouping():
    """PG14 не поддерживает IGNORE NULLS — используется COUNT-based grouping."""
    assert "COUNT(open_interest)" in str(FFILL_CTE_COLUMNS)
    assert "_oi_grp" in str(FFILL_CTE_COLUMNS)


def test_ffill_select_extracts_via_max_over_partition():
    assert "MAX(open_interest)" in str(FFILL_SELECT_COLUMNS)
    assert "PARTITION BY _oi_grp" in str(FFILL_SELECT_COLUMNS)
    assert "oi_ffill" in str(FFILL_SELECT_COLUMNS)


def test_step_ms_resolution():
    assert resolve_step_ms_for_timeframe("1m") == 60_000
    assert resolve_step_ms_for_timeframe("5m") == 300_000
    assert resolve_step_ms_for_timeframe("60m") == 3_600_000
    assert resolve_step_ms_for_timeframe("1d") == 86_400_000
    with pytest.raises(ValueError):
        resolve_step_ms_for_timeframe("999m")


def test_target_bars_scales_with_timeframe():
    """target_return_1 bars = max(1, round(3h / step)). Проверяем генерацию SQL."""
    # 5m: 36 bars, 60m: 3 bars, 1d: 1 bar (round(3h/24h)=0 → max(1,0)=1)
    fs_5m = build_feature_select_clause(step_ms=300_000, add_target=True)
    fs_1d = build_feature_select_clause(step_ms=86_400_000, add_target=True)
    s5  = str(fs_5m.select_clause)
    s1d = str(fs_1d.select_clause)
    assert "LEAD" in s5
    assert "LEAD" in s1d
    # числа bars — psycopg2.sql.Literal даёт repr вида "Literal(36)"
    assert "Literal(36)" in s5
    assert "Literal(1)" in s1d


def test_pipeline_sql_importable_and_signature():
    """pipeline_sql.upsert_with_sql_features существует и имеет ожидаемую сигнатуру."""
    from backend.dataset import pipeline_sql
    sig = inspect.signature(pipeline_sql.upsert_with_sql_features)
    params = list(sig.parameters.keys())
    assert params[:6] == [
        "connection", "table_name", "raw_rows",
        "warmup_start_ms", "write_start_ms", "timeframe",
    ]


def test_pipeline_sql_merge_preserves_is_distinct_from():
    """Fix A из Round 2 (IS DISTINCT FROM) должен быть в pipeline_sql."""
    from backend.dataset import pipeline_sql
    source = inspect.getsource(pipeline_sql.upsert_with_sql_features)
    assert "IS DISTINCT FROM" in source
    assert "RETURNING (xmax = 0)" in source or "RETURNING (xmax=0)" in source


def test_pipeline_sql_uses_raw_only_staging():
    """Staging table использует только 8 raw-колонок, не тянет 50-колоночную схему."""
    from backend.dataset import pipeline_sql
    source = inspect.getsource(pipeline_sql)
    # _RAW_STAGE_COLUMNS — tuple из 8 имён
    assert pipeline_sql._RAW_STAGE_COLUMNS == (
        "timestamp_utc", "symbol", "exchange", "timeframe",
        "close_price", "funding_rate", "open_interest", "rsi",
    )
