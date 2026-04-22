from __future__ import annotations

import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values

from .constants import (
    DATASET_COLUMN_NAMES,
    EXPECTED_TABLE_SCHEMA,
    FORBIDDEN_TABLE_COLUMNS,
    REQUIRED_NOT_NULL_COLUMNS,
    TEXT_DATASET_COLUMNS,
    UPSERT_BATCH_SIZE,
)
from .core import log, ms_to_datetime
from .timelog import now, tlog

# Предвычисленные метаданные колонок — избегают повторных операций в fetch_db_rows
_COL_NAMES: tuple[str, ...] = tuple(DATASET_COLUMN_NAMES)
_N_COLS: int = len(DATASET_COLUMN_NAMES)
# Индексы, где значение возвращается как есть (timestamp_utc + текстовые колонки)
_PASSTHROUGH_INDICES: frozenset[int] = frozenset(
    i for i, name in enumerate(DATASET_COLUMN_NAMES)
    if name == "timestamp_utc" or name in TEXT_DATASET_COLUMNS
)


def table_exists(connection: psycopg2.extensions.connection, table_name: str) -> bool:
    """Проверяет наличие таблицы в PostgreSQL."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
        return cursor.fetchone()[0] is not None


def read_table_schema(connection: psycopg2.extensions.connection, table_name: str) -> list[tuple[str, str]]:
    """Читает текущую схему таблицы из information_schema."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]


def create_market_table(
    connection: psycopg2.extensions.connection,
    table_name: str,
    if_not_exists: bool = False,
) -> None:
    """Создает таблицу датасета с ожидаемой схемой."""
    clause = "IF NOT EXISTS " if if_not_exists else ""
    column_defs = []
    for column_name, data_type in EXPECTED_TABLE_SCHEMA:
        not_null = " NOT NULL" if column_name in REQUIRED_NOT_NULL_COLUMNS else ""
        column_defs.append(f"{column_name} {data_type}{not_null}")
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                f"""
                CREATE TABLE {clause}{{}} (
                    {", ".join(column_defs)},
                    PRIMARY KEY (timestamp_utc)
                )
                """
            ).format(sql.Identifier(table_name))
        )
    connection.commit()


def ensure_dataset_schema(connection: psycopg2.extensions.connection, table_name: str) -> tuple[list[str], list[str]]:
    """Добавляет недостающие колонки датасета и удаляет запрещенные колонки."""
    schema = read_table_schema(connection, table_name)
    existing = {column_name for column_name, _ in schema}
    added_columns: list[str] = []
    dropped_columns: list[str] = []

    with connection.cursor() as cursor:
        for column_name in sorted(existing & FORBIDDEN_TABLE_COLUMNS):
            cursor.execute(
                sql.SQL("ALTER TABLE {} DROP COLUMN IF EXISTS {}").format(
                    sql.Identifier(table_name),
                    sql.Identifier(column_name),
                )
            )
            dropped_columns.append(column_name)

        for column_name, data_type in EXPECTED_TABLE_SCHEMA:
            if column_name in existing:
                continue
            cursor.execute(
                sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}").format(
                    sql.Identifier(table_name),
                    sql.Identifier(column_name),
                    sql.SQL(data_type),
                )
            )
            added_columns.append(column_name)

    connection.commit()
    return added_columns, dropped_columns


def ensure_table(connection: psycopg2.extensions.connection, table_name: str) -> None:
    """Создает таблицу для демо-датасета, если ее нет."""
    create_market_table(connection, table_name, if_not_exists=True)
    ensure_dataset_schema(connection, table_name)


def validate_database(connection: psycopg2.extensions.connection, table_name: str = "market_data") -> dict:
    """Проверяет схему таблицы и очищает поврежденные данные."""
    schema = read_table_schema(connection, table_name)
    table_recreated = False
    table_dropped = False
    added_columns: list[str] = []
    dropped_columns: list[str] = []

    if not schema:
        create_market_table(connection, table_name)
        table_recreated = True
    else:
        added_columns, dropped_columns = ensure_dataset_schema(connection, table_name)

    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("DELETE FROM {} WHERE index_price IS NULL OR timestamp_utc IS NULL").format(
                sql.Identifier(table_name)
            )
        )
        deleted_null_rows = max(cursor.rowcount, 0)
        cursor.execute(
            sql.SQL(
                """
                DELETE FROM {table_name} AS target
                USING (
                    SELECT ctid
                    FROM (
                        SELECT
                            ctid,
                            ROW_NUMBER() OVER (
                                PARTITION BY timestamp_utc, symbol, timeframe
                                ORDER BY ctid DESC
                            ) AS row_number
                        FROM {table_name}
                    ) ranked
                    WHERE row_number > 1
                ) duplicates
                WHERE target.ctid = duplicates.ctid
                """
            ).format(table_name=sql.Identifier(table_name))
        )
        deleted_duplicate_rows = max(cursor.rowcount, 0)
    connection.commit()

    final_schema = read_table_schema(connection, table_name)
    report = {
        "table_name": table_name,
        "table_dropped": table_dropped,
        "table_recreated": table_recreated,
        "added_columns": added_columns,
        "dropped_columns": dropped_columns,
        "deleted_null_rows": deleted_null_rows,
        "deleted_duplicate_rows": deleted_duplicate_rows,
        "schema": final_schema,
    }
    log(
        f"Validation for {table_name}: dropped={table_dropped}, recreated={table_recreated}, "
        f"added_columns={len(added_columns)}, dropped_columns={len(dropped_columns)}, "
        f"deleted_null_rows={deleted_null_rows}, deleted_duplicate_rows={deleted_duplicate_rows}"
    )
    log(
        "Schema after validation: "
        + ", ".join(f"{column_name} {data_type}" for column_name, data_type in final_schema)
    )
    return report


def fetch_db_rows(connection: psycopg2.extensions.connection, table_name: str, start_ms: int, end_ms: int) -> dict[int, dict]:
    """Читает строки из таблицы по диапазону времени."""
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT {columns}
                FROM {}
                WHERE timestamp_utc BETWEEN %s AND %s
                ORDER BY timestamp_utc
                """
            ).format(
                sql.Identifier(table_name),
                columns=sql.SQL(", ").join(sql.Identifier(column_name) for column_name in DATASET_COLUMN_NAMES),
            ),
            (ms_to_datetime(start_ms), ms_to_datetime(end_ms)),
        )
        result = {}
        col_names = _COL_NAMES
        passthrough = _PASSTHROUGH_INDICES
        for row in cursor.fetchall():
            ts_ms = int(row[0].timestamp() * 1000)
            result[ts_ms] = {
                col_names[i]: row[i] if i in passthrough else (float(row[i]) if row[i] is not None else None)
                for i in range(_N_COLS)
            }
        return result


def upsert_rows(
    connection: psycopg2.extensions.connection,
    table_name: str,
    rows: list[dict],
    on_batch=None,
) -> tuple[int, int]:
    """Пишет строки в PostgreSQL через двухфазный UPSERT.

    Фаза 1: данные батчами заливаются во временную staging-таблицу без
    индексов и проверок конфликтов — это на порядок быстрее прямого INSERT.

    Фаза 2: один запрос INSERT … SELECT … ON CONFLICT сливает staging в
    основную таблицу — конфликт-чек выполняется одним проходом по B-дереву,
    а не N раз по одной строке.

    ``on_batch(written, total)`` вызывается после каждого батча в staging.
    """
    columns = DATASET_COLUMN_NAMES
    update_columns = [c for c in columns if c != "timestamp_utc"]
    total = len(rows)
    tmp_table = f"_upsert_stage_{table_name}"

    t0 = now()
    tlog.info("upsert_rows | START table=%s rows=%d batch_size=%d", table_name, total, UPSERT_BATCH_SIZE)

    # ── Фаза 1: staging temp table ───────────────────────────────────────────
    # CREATE … (LIKE main) копирует имена+типы+NOT NULL, но НЕ PK и НЕ индексы,
    # поэтому вставка без проверок — максимально быстро.
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("CREATE TEMP TABLE IF NOT EXISTS {} (LIKE {})").format(
                sql.Identifier(tmp_table),
                sql.Identifier(table_name),
            )
        )
        cursor.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier(tmp_table)))

    insert_stmt = sql.SQL(
        "INSERT INTO {} ({columns}) VALUES %s"
    ).format(
        sql.Identifier(tmp_table),
        columns=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
    ).as_string(connection)

    t_staging = now()
    batches = 0
    for offset in range(0, total, UPSERT_BATCH_SIZE):
        batch = rows[offset : offset + UPSERT_BATCH_SIZE]
        values = [tuple(row.get(c) for c in columns) for row in batch]
        with connection.cursor() as cursor:
            execute_values(cursor, insert_stmt, values, page_size=len(values))
        batches += 1
        if on_batch is not None:
            on_batch(min(offset + UPSERT_BATCH_SIZE, total), total)
    tlog.info("upsert_rows | staging done batches=%d elapsed=%.3fs", batches, now() - t_staging)

    # ── Фаза 2: merge staging → main (один конфликт-чек на весь набор) ───────
    merge_stmt = sql.SQL(
        """
        INSERT INTO {main} ({columns})
        SELECT {columns} FROM {tmp}
        ON CONFLICT (timestamp_utc)
        DO UPDATE SET {updates}
        RETURNING (xmax = 0) AS inserted
        """
    ).format(
        main=sql.Identifier(table_name),
        tmp=sql.Identifier(tmp_table),
        columns=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        updates=sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(
                sql.Identifier(c),
                sql.Identifier(c),
            )
            for c in update_columns
        ),
    )
    t_merge = now()
    with connection.cursor() as cursor:
        cursor.execute(merge_stmt)
        flags = cursor.fetchall()

    connection.commit()

    inserted = sum(1 for (flag,) in flags if flag)
    updated = len(flags) - inserted
    tlog.info(
        "upsert_rows | DONE table=%s inserted=%d updated=%d merge_elapsed=%.3fs total_elapsed=%.3fs",
        table_name, inserted, updated, now() - t_merge, now() - t0,
    )
    return inserted, updated
