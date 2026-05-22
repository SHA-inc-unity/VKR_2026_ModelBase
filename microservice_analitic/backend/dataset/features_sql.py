"""SQL-based feature engineering for the dataset pipeline (Round 3).

Вся feature-инженерия, которая в `backend.dataset.features` выполняется
pandas-ом (lag, rolling, log-returns, time-of-day, oi/funding ffill и т.д.),
переписана как набор оконных функций PostgreSQL. Это позволяет:

- не материализовать в Python DataFrame ~1.26 ГБ feature-матрицы (3M × 50 float64);
- не держать ~1.5 ГБ list-of-dicts с raw-строками;
- выполнить feature-вычисления **на стороне БД** параллельно работе Python;
- сделать path "staging raw → INSERT main (с features, вычисленными в SQL)
  одним merge-statement" — без roundtrip всех данных в Python.

Модуль сам **не** выполняет SQL — он только собирает фрагменты (`sql.Composed`)
из `psycopg2.sql`, которые затем вкладываются в merge-statement из pipeline_sql.

Ограничения и выбранные компромиссы:

- RSI остаётся в Python. Wilder EWM — рекурсивная схема, в PostgreSQL
  выражается через recursive CTE, что медленно на больших окнах и даёт
  численное расхождение с pandas (порядок операций с плавающей точкой).
  После Round 2 Fix B RSI считается только на узком окне — это дёшево.
- Строковое сравнение с pandas идентично **по формулам**:
    * pct_change(k) == price / LAG(price, k) - 1;
    * np.log(clip(price/prev, 1e-10, inf)) == LN(GREATEST(price/prev, 1e-10));
    * Series.rolling(w, min_periods=1).mean() == AVG(x) OVER (ROWS BETWEEN w-1 PRECEDING AND CURRENT ROW);
    * Series.rolling(w, min_periods=1).std(ddof=0) == STDDEV_POP(x) OVER (...);
    * Series.ffill() в PG14 реализуется через count-based grouping (IGNORE NULLS
      появляется только в PG16).
- Все NULL-безопасные деления через NULLIF(...,0).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from psycopg2 import sql

from .constants import (
    RETURN_HORIZONS,
    ROLLING_WINDOWS,
    RSI_LAG_STEPS,
    TARGET_HORIZON_MS,
    TIMEFRAMES,
)


# ---------------------------------------------------------------------------
# Низкоуровневые SQL-фрагменты (маленькие кирпичи для сборки)
# ---------------------------------------------------------------------------

def _lag(col: str, k: int, alias: str) -> sql.Composed:
    """LAG(col, k) OVER w AS alias."""
    return sql.SQL("LAG({c}, {k}) OVER w AS {a}").format(
        c=sql.Identifier(col), k=sql.Literal(k), a=sql.Identifier(alias),
    )


def _rolling(agg: str, col: str, window: int, alias: str) -> sql.Composed:
    """AGG(col) OVER (ORDER BY ts ROWS BETWEEN w-1 PRECEDING AND CURRENT ROW)."""
    if agg not in {"AVG", "STDDEV_POP", "MIN", "MAX"}:
        raise ValueError(f"Unsupported rolling aggregate: {agg}")
    return sql.SQL(
        "{agg}({c}) OVER (ORDER BY timestamp_utc "
        "ROWS BETWEEN {prev} PRECEDING AND CURRENT ROW) AS {a}"
    ).format(
        agg=sql.SQL(agg),
        c=sql.Identifier(col),
        prev=sql.Literal(window - 1),
        a=sql.Identifier(alias),
    )


def _pct_change(col: str, k: int, alias: str) -> sql.Composed:
    """(col - LAG(col,k))/NULLIF(LAG(col,k),0) AS alias — pandas pct_change(k)."""
    return sql.SQL(
        "({c} - LAG({c}, {k}) OVER w) / NULLIF(LAG({c}, {k}) OVER w, 0) AS {a}"
    ).format(
        c=sql.Identifier(col), k=sql.Literal(k), a=sql.Identifier(alias),
    )


def _log_return(col: str, k: int, alias: str) -> sql.Composed:
    """LN(GREATEST(col/NULLIF(LAG(col,k),0), 1e-10)) AS alias — совпадает с np.log(clip(price/prev, 1e-10, inf))."""
    return sql.SQL(
        "LN(GREATEST({c} / NULLIF(LAG({c}, {k}) OVER w, 0), 1e-10)) AS {a}"
    ).format(
        c=sql.Identifier(col), k=sql.Literal(k), a=sql.Identifier(alias),
    )


def _lead(col: str, k: int, alias: str) -> sql.Composed:
    """LEAD(col, k) OVER w — для target_return_1 (price_{t+N}/price_t - 1).

    Реализация через LEAD делает pandas-эквивалент:
        price.pct_change(bars).shift(-bars)
        = (price - price.shift(bars)) / price.shift(bars) then shifted back by -bars
        = (LEAD(price,bars) - price) / price

    В pandas при `price.shift(bars) == 0` значение становится inf→NaN через
    деление; здесь используем NULLIF для симметрии.
    """
    return sql.SQL(
        "(LEAD({c}, {k}) OVER w - {c}) / NULLIF({c}, 0) AS {a}"
    ).format(
        c=sql.Identifier(col), k=sql.Literal(k), a=sql.Identifier(alias),
    )


# ---------------------------------------------------------------------------
# ffill — эмуляция для PG14 через count-based grouping
# ---------------------------------------------------------------------------

# В pandas `funding_rate.ffill()` и `open_interest.ffill()` протягивают последнее
# не-NULL значение вниз. В PostgreSQL 14 нет `IGNORE NULLS` для LAG/LAST_VALUE
# (IGNORE NULLS появилось только в PG16). Идиоматический приём:
#
#     SELECT *,
#       COUNT(funding_rate) OVER (ORDER BY ts) AS _fundgrp,
#       COUNT(open_interest) OVER (ORDER BY ts) AS _oigrp
#     FROM combined
#
# COUNT игнорирует NULL, поэтому новое значение "появляется" только когда
# встречается не-NULL. В этом prefix-count все строки от последнего не-NULL
# до следующего не-NULL делят одинаковый groupid. Затем:
#
#     MAX(funding_rate) OVER (PARTITION BY _fundgrp)
#
# возвращает единственное не-NULL в группе — т.е. ffill.
#
# В пустой префиксной зоне (до первого не-NULL) COUNT=0; MAX по группе 0
# → NULL. Это корректно совпадает с pandas: первые значения остаются NaN.

FFILL_CTE_COLUMNS = sql.SQL(
    """
    COUNT(open_interest) OVER (ORDER BY timestamp_utc) AS _oi_grp
    """
)

FFILL_SELECT_COLUMNS = sql.SQL(
    """
    MAX(open_interest) OVER (PARTITION BY _oi_grp) AS oi_ffill
    """
)


# ---------------------------------------------------------------------------
# Построение всего списка feature-колонок
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeatureSelect:
    """Результат сборки: SQL-фрагмент SELECT-list и список имён колонок.

    Использование (из pipeline_sql):
        fs = build_feature_select_clause(step_ms, target_horizon_ms)
        # fs.column_names — 42 имени feature-колонок
        # fs.select_clause — sql.Composed со списком "expr AS name" через запятую
    """
    column_names: tuple[str, ...]
    select_clause: sql.Composed


def _target_bars(step_ms: int, target_horizon_ms: int = TARGET_HORIZON_MS) -> int:
    """Число баров для target_return_1. Совпадает с pandas _compute_group_features."""
    return max(1, round(target_horizon_ms / step_ms))


def build_feature_select_clause(
    step_ms: int,
    target_horizon_ms: int = TARGET_HORIZON_MS,
    add_target: bool = True,
) -> FeatureSelect:
    """Собирает SELECT-list, вычисляющий одобренный набор 27 feature-колонок
    window-функциями (+ target_return_1, если add_target).

    Семантически эквивалентно `features._compute_group_features`:
      - return_{RETURN_HORIZONS}, log_return_{RETURN_HORIZONS}
      - price_roll{w}_{mean,std,min,max}, price_to_roll{w}_mean, price_vol_{w}
      - oi_roll{w}_mean, oi_return_1
      - rsi_lag_{RSI_LAG_STEPS}
      - hour_sin, hour_cos, dow_sin, dow_cos
      - target_return_1 (если add_target)

    Все оконные функции используют общий именованный WINDOW:
        WINDOW w AS (ORDER BY timestamp_utc)

    который вызывающий код должен добавить в итоговый SELECT. Для rolling-агрегатов
    используется inline-frame "ROWS BETWEEN N-1 PRECEDING AND CURRENT ROW".

    Важное предусловие: источник должен уже содержать `oi_ffill` колонку
    (результат FFILL_SELECT_COLUMNS на слое ниже).
    """
    parts: list[sql.Composed] = []
    names: list[str] = []

    def add(name: str, expr: sql.Composed) -> None:
        names.append(name)
        parts.append(expr)

    # return_{h}, log_return_{h}
    for h in RETURN_HORIZONS:
        add(f"return_{h}", _pct_change("close_price", h, f"return_{h}"))
        add(f"log_return_{h}", _log_return("close_price", h, f"log_return_{h}"))

    # price_roll{w}_{mean,std,min,max}, price_to_roll{w}_mean, price_vol_{w}
    for w in ROLLING_WINDOWS:
        add(f"price_roll{w}_mean", _rolling("AVG", "close_price", w, f"price_roll{w}_mean"))
        add(f"price_roll{w}_std",  _rolling("STDDEV_POP", "close_price", w, f"price_roll{w}_std"))
        add(f"price_roll{w}_min",  _rolling("MIN", "close_price", w, f"price_roll{w}_min"))
        add(f"price_roll{w}_max",  _rolling("MAX", "close_price", w, f"price_roll{w}_max"))
        add(
            f"price_to_roll{w}_mean",
            sql.SQL(
                "close_price / NULLIF(AVG(close_price) OVER "
                "(ORDER BY timestamp_utc ROWS BETWEEN {p} PRECEDING AND CURRENT ROW), 0) "
                "AS {a}"
            ).format(p=sql.Literal(w - 1), a=sql.Identifier(f"price_to_roll{w}_mean")),
        )
        add(
            f"price_vol_{w}",
            sql.SQL(
                "STDDEV_POP(close_price) OVER "
                "(ORDER BY timestamp_utc ROWS BETWEEN {p} PRECEDING AND CURRENT ROW) "
                "/ NULLIF(AVG(close_price) OVER "
                "(ORDER BY timestamp_utc ROWS BETWEEN {p} PRECEDING AND CURRENT ROW), 0) "
                "AS {a}"
            ).format(p=sql.Literal(w - 1), a=sql.Identifier(f"price_vol_{w}")),
        )

    # oi_roll{w}_mean, oi_return_1 — на ffill-колонке OI
    for w in ROLLING_WINDOWS:
        add(f"oi_roll{w}_mean", _rolling("AVG", "oi_ffill", w, f"oi_roll{w}_mean"))
    add("oi_return_1", _pct_change("oi_ffill", 1, "oi_return_1"))

    # rsi_lag_{k} — на исходной rsi
    for k in RSI_LAG_STEPS:
        add(f"rsi_lag_{k}", _lag("rsi", k, f"rsi_lag_{k}"))

    # hour_sin/cos, dow_sin/cos — UTC-тайм-фичи
    pi2 = 2.0 * math.pi
    add("hour_sin", sql.SQL(
        "SIN({k} * EXTRACT(HOUR FROM timestamp_utc AT TIME ZONE 'UTC') / 24.0) AS hour_sin"
    ).format(k=sql.Literal(pi2)))
    add("hour_cos", sql.SQL(
        "COS({k} * EXTRACT(HOUR FROM timestamp_utc AT TIME ZONE 'UTC') / 24.0) AS hour_cos"
    ).format(k=sql.Literal(pi2)))
    add("dow_sin", sql.SQL(
        "SIN({k} * (EXTRACT(ISODOW FROM timestamp_utc AT TIME ZONE 'UTC') - 1) / 7.0) AS dow_sin"
    ).format(k=sql.Literal(pi2)))
    add("dow_cos", sql.SQL(
        "COS({k} * (EXTRACT(ISODOW FROM timestamp_utc AT TIME ZONE 'UTC') - 1) / 7.0) AS dow_cos"
    ).format(k=sql.Literal(pi2)))

    # target_return_1 — только если add_target
    if add_target:
        bars = _target_bars(step_ms, target_horizon_ms)
        add("target_return_1", _lead("close_price", bars, "target_return_1"))

    select_clause = sql.SQL(",\n    ").join(parts)
    return FeatureSelect(
        column_names=tuple(names),
        select_clause=select_clause,
    )


def resolve_step_ms_for_timeframe(timeframe: str) -> int:
    """Возвращает step_ms для таймфрейма (нужно pipeline_sql для target_bars)."""
    tf = timeframe.lower()
    if tf in TIMEFRAMES:
        return int(TIMEFRAMES[tf][1])
    raise ValueError(f"Unknown timeframe: {timeframe!r}")
