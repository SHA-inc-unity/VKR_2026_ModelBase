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
        for row in cursor.fetchall():
            timestamp_ms = int(row[0].timestamp() * 1000)
            row_dict = {}
            for column_name, value in zip(DATASET_COLUMN_NAMES, row):
                if column_name == "timestamp_utc":
                    row_dict[column_name] = value
                elif column_name in TEXT_DATASET_COLUMNS:
                    row_dict[column_name] = value
                else:
                    row_dict[column_name] = float(value) if value is not None else None
            result[timestamp_ms] = row_dict
        return result


def upsert_rows(connection: psycopg2.extensions.connection, table_name: str, rows: list[dict]) -> tuple[int, int]:
    """Пишет строки в PostgreSQL через UPSERT."""
    columns = DATASET_COLUMN_NAMES
    update_columns = [column_name for column_name in columns if column_name != "timestamp_utc"]
    statement = sql.SQL(
        """
        INSERT INTO {} ({columns}) VALUES %s
        ON CONFLICT (timestamp_utc)
        DO UPDATE SET {updates}
        RETURNING (xmax = 0) AS inserted
        """
    ).format(
        sql.Identifier(table_name),
        columns=sql.SQL(", ").join(sql.Identifier(column_name) for column_name in columns),
        updates=sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(
                sql.Identifier(column_name),
                sql.Identifier(column_name),
            )
            for column_name in update_columns
        ),
    )
    statement_sql = statement.as_string(connection)
    inserted = 0
    updated = 0
    for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[offset : offset + UPSERT_BATCH_SIZE]
        values = [tuple(row.get(column_name) for column_name in columns) for row in batch]
        with connection.cursor() as cursor:
            execute_values(cursor, statement_sql, values, page_size=len(values))
            flags = cursor.fetchall()
        batch_inserted = sum(1 for flag, in flags if flag)
        inserted += batch_inserted
        updated += len(flags) - batch_inserted
    connection.commit()
    return inserted, updated
