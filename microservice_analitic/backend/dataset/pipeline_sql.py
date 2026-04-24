"""SQL-first upsert pipeline (Round 3) — полный перенос feature-инженерии в PostgreSQL.

Этот модуль — альтернатива связке
`rebuild_rsi_and_upsert_rows → build_features → upsert_dataframe` из
`backend.dataset.pipeline`. Он работает в три шага:

1. **RSI в Python (малый объём)** — Wilder EWM для узкого окна raw-строк. После
   Round 2 Fix B это редко более ~1000 строк за вызов.
2. **COPY raw в staging** — только 8 колонок (timestamp_utc, symbol, exchange,
   timeframe, index_price, funding_rate, open_interest, rsi). Python не
   материализует feature-матрицу.
3. **Один SQL-statement** — с WITH CTE:
      - UNION ALL raw-staging + warmup-rows из main (через ANTI-JOIN по
        timestamp_utc, без дубликатов);
      - count-based grouping для ffill funding_rate/open_interest (PG14);
      - window-functions для всех 42 feature-колонок
        (см. `backend.dataset.features_sql`);
      - `INSERT ... ON CONFLICT DO UPDATE ... WHERE ... IS DISTINCT FROM ...`
        — пропуск no-op UPDATE'ов (Fix A из Round 2).
      - `RETURNING (xmax=0)` — корректный счётчик inserted vs updated.

**Важно**: модуль опциональный. По умолчанию `download_missing` продолжает
использовать pandas-путь. Включается явным флагом (environment variable
`DOWNLOAD_MISSING_USE_SQL_FEATURES=1`) или прямым вызовом.

Что **не** меняется:
- бизнес-семантика features: все формулы совпадают с `features.build_features`
  (см. docstring `features_sql.build_feature_select_clause`);
- RSI считается в Python, байт-идентично старому пути;
- схема таблиц, PK, индексы, UPSERT семантика сохранены.
"""
from __future__ import annotations

import csv
import io

import psycopg2
from psycopg2 import sql

from .constants import (
    DATASET_COLUMN_NAMES,
    FEATURE_TABLE_SCHEMA,
    UPSERT_BATCH_SIZE,
)
from .features_sql import (
    FFILL_CTE_COLUMNS,
    FFILL_SELECT_COLUMNS,
    build_feature_select_clause,
    resolve_step_ms_for_timeframe,
)
from .timelog import now, tlog


# 8 raw-колонок, которые COPY-ятся в staging (остальные 42 вычисляются в SQL).
_RAW_STAGE_COLUMNS: tuple[str, ...] = (
    "timestamp_utc",
    "symbol",
    "exchange",
    "timeframe",
    "index_price",
    "funding_rate",
    "open_interest",
    "rsi",
)


def _feature_column_names() -> tuple[str, ...]:
    """42 имени feature-колонок в порядке FEATURE_TABLE_SCHEMA."""
    return tuple(name for name, _ in FEATURE_TABLE_SCHEMA)


def upsert_with_sql_features(
    connection: psycopg2.extensions.connection,
    table_name: str,
    raw_rows: list[dict],
    warmup_start_ms: int,
    write_start_ms: int,
    timeframe: str,
    on_upsert_batch=None,
) -> tuple[int, int]:
    """Пишет raw-строки в main, вычисляя все features в SQL, за один roundtrip.

    Parameters
    ----------
    raw_rows
        Список dict'ов с ключами из `_RAW_STAGE_COLUMNS`. RSI должен быть
        уже вычислен (обычно через `pipeline.rebuild_rsi`).
    warmup_start_ms
        Начало warmup-окна; из main будут подтянуты строки в диапазоне
        [warmup_start_ms, min(raw_rows.ts)) для контекста оконных функций.
        Рекомендация: warmup_start_ms = raw_rows[0].ts − 64 * step_ms
        (покрывает max(LAG_STEPS=24, ROLLING_WINDOWS=24) + запас).
    write_start_ms
        Нижняя граница для реальной записи. Feature-строки с ts < write_start_ms
        используются только как warmup-контекст и в main не записываются.
        Обычно равен timestamp первого пропущенного значения (Fix B из Round 2).
    timeframe
        Имя TF ("1m", "5m", ..., "1d") — нужно для корректного `target_return_1`.

    Returns
    -------
    (inserted, updated): tuple[int, int]
        Число реально изменённых строк. Разность
        `len(raw_rows) - inserted - updated - ... = skipped` логируется.

    Notes
    -----
    Warmup подтягивается ANTI-JOIN'ом — ни одна строка из warmup не будет
    записана обратно в main (она уже там). Записываются только строки,
    чьи timestamps лежат в staging И в диапазоне [write_start_ms, +∞).
    """
    if not raw_rows:
        tlog.info("upsert_with_sql_features | SKIP table=%s reason=empty_raw_rows", table_name)
        return 0, 0

    t0 = now()
    total_raw = len(raw_rows)
    step_ms = resolve_step_ms_for_timeframe(timeframe)
    tlog.info(
        "upsert_with_sql_features | START table=%s raw=%d warmup_start=%d write_start=%d tf=%s",
        table_name, total_raw, warmup_start_ms, write_start_ms, timeframe,
    )

    stage_table = f"_upsert_stage_raw_{table_name}"

    # ── Phase 1: staging temp table (8 cols only, no indexes) ───────────────
    # LIKE-клонировать всю main нельзя (50+ колонок, 42 из них NULL на входе
    # дадут исключение NOT NULL или просто лишние столбцы). Явно описываем
    # минимальную staging-схему.
    raw_schema_sql = sql.SQL(",\n        ").join(
        sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(dt))
        for c, dt in (
            ("timestamp_utc",   "timestamp with time zone PRIMARY KEY"),
            ("symbol",          "character varying"),
            ("exchange",        "character varying"),
            ("timeframe",       "character varying"),
            ("index_price",     "numeric"),
            ("funding_rate",    "numeric"),
            ("open_interest",   "numeric"),
            ("rsi",             "numeric"),
        )
    )
    with connection.cursor() as cur:
        cur.execute(
            sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(stage_table))
        )
        cur.execute(
            sql.SQL("CREATE TEMP TABLE {} (\n        {}\n        )").format(
                sql.Identifier(stage_table), raw_schema_sql,
            )
        )

    copy_stmt = sql.SQL(
        "COPY {} ({}) FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', NULL '')"
    ).format(
        sql.Identifier(stage_table),
        sql.SQL(", ").join(sql.Identifier(c) for c in _RAW_STAGE_COLUMNS),
    ).as_string(connection)

    t_stage = now()
    batches = 0
    for offset in range(0, total_raw, UPSERT_BATCH_SIZE):
        batch = raw_rows[offset : offset + UPSERT_BATCH_SIZE]
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter='\t', quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
        for row in batch:
            writer.writerow([
                v.isoformat() if hasattr(v, 'isoformat')
                else ('' if v is None else v)
                for v in (row.get(c) for c in _RAW_STAGE_COLUMNS)
            ])
        buf.seek(0)
        with connection.cursor() as cur:
            cur.copy_expert(copy_stmt, buf)
        batches += 1
        if on_upsert_batch is not None:
            on_upsert_batch(min(offset + UPSERT_BATCH_SIZE, total_raw), total_raw)
    tlog.info(
        "upsert_with_sql_features | staging done batches=%d elapsed=%.3fs",
        batches, now() - t_stage,
    )

    # ── Phase 2: single SQL merge with feature computation ──────────────────
    fs = build_feature_select_clause(step_ms=step_ms, add_target=True)
    all_cols: tuple[str, ...] = tuple(DATASET_COLUMN_NAMES)
    update_cols: tuple[str, ...] = tuple(c for c in all_cols if c != "timestamp_utc")

    # ID-списки для разных мест SQL.
    id_all        = sql.SQL(", ").join(sql.Identifier(c) for c in all_cols)
    id_raw_stage  = sql.SQL(", ").join(sql.Identifier(c) for c in _RAW_STAGE_COLUMNS)
    id_set        = sql.SQL(", ").join(
        sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
        for c in update_cols
    )
    id_m_cols = sql.SQL(", ").join(
        sql.SQL("m.{}").format(sql.Identifier(c)) for c in update_cols
    )
    id_excluded_cols = sql.SQL(", ").join(
        sql.SQL("EXCLUDED.{}").format(sql.Identifier(c)) for c in update_cols
    )

    # combined: raw-staging + warmup-from-main (ANTI-JOIN).
    # with_grp: prefix-count для ffill-groups.
    # with_ffill: MAX(...) OVER partition — даёт funding_ffill/oi_ffill.
    # features: все 42 feature-колонки через window-функции.
    # Финал: INSERT ... SELECT ... ON CONFLICT DO UPDATE ... WHERE IS DISTINCT FROM.
    #
    # Переменные параметров (psycopg2 %s):
    #   %s(warmup_start_dt), %s(write_start_dt) — передаются в execute().
    merge_stmt = sql.SQL(
        """
        WITH combined AS (
            SELECT {raw_cols} FROM {stage}
            UNION ALL
            SELECT {raw_cols} FROM {main} m
            WHERE m.timestamp_utc >= %s
              AND NOT EXISTS (
                  SELECT 1 FROM {stage} s WHERE s.timestamp_utc = m.timestamp_utc
              )
        ),
        with_grp AS (
            SELECT *,
                {ffill_cte}
            FROM combined
        ),
        with_ffill AS (
            SELECT *,
                {ffill_sel}
            FROM with_grp
        ),
        features AS (
            SELECT
                timestamp_utc, symbol, exchange, timeframe,
                index_price, funding_rate, open_interest, rsi,
                {feature_select}
            FROM with_ffill
        )
        INSERT INTO {main} AS m ({all_cols})
        SELECT {all_cols} FROM features
        WHERE timestamp_utc >= %s
        ON CONFLICT (timestamp_utc)
        DO UPDATE SET {updates}
        WHERE ({m_cols}) IS DISTINCT FROM ({excluded_cols})
        RETURNING (xmax = 0) AS inserted
        """
    ).format(
        raw_cols=id_raw_stage,
        stage=sql.Identifier(stage_table),
        main=sql.Identifier(table_name),
        ffill_cte=FFILL_CTE_COLUMNS,
        ffill_sel=FFILL_SELECT_COLUMNS,
        feature_select=fs.select_clause,
        all_cols=id_all,
        updates=id_set,
        m_cols=id_m_cols,
        excluded_cols=id_excluded_cols,
    )

    t_merge = now()
    from .core import ms_to_datetime
    with connection.cursor() as cur:
        cur.execute(
            merge_stmt,
            (ms_to_datetime(warmup_start_ms), ms_to_datetime(write_start_ms)),
        )
        flags = cur.fetchall()
    connection.commit()

    inserted = sum(1 for (f,) in flags if f)
    updated = len(flags) - inserted
    in_write_range = sum(
        1 for r in raw_rows
        if r["timestamp_utc"].timestamp() * 1000 >= write_start_ms
    ) if raw_rows and hasattr(raw_rows[0]["timestamp_utc"], "timestamp") else total_raw
    skipped = in_write_range - inserted - updated
    tlog.info(
        "upsert_with_sql_features | DONE table=%s raw=%d in_write_range=%d "
        "inserted=%d updated=%d skipped_noop=%d merge=%.3fs total=%.3fs",
        table_name, total_raw, in_write_range,
        inserted, updated, skipped,
        now() - t_merge, now() - t0,
    )
    return inserted, updated
