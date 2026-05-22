"""CSV-экспорт датасета через серверный `COPY (...) TO STDOUT`.

Быстрый путь: PostgreSQL сам сериализует строки в CSV и шлёт поток через
`copy_expert`. Python выступает как тонкий байт-транспорт — не строится ни
DataFrame, ни список словарей.

Использование:
    with connect_db(config) as conn:
        data = export_dataset_csv(
            conn,
            table_name="btcusdt_5m",
            start_ts_utc="2024-01-01",
            end_ts_utc="2024-02-01",
        )
    st.download_button("Скачать CSV", data=data, file_name="btcusdt_5m.csv",
                       mime="text/csv")
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Sequence

import psycopg2
from psycopg2 import sql


# Разрешённые тайм-форматы
_Timestamp = datetime | str | None


def _parse_ts(value: _Timestamp) -> datetime | None:
    """Нормализует границу диапазона в timezone-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # fromisoformat в 3.12 понимает "Z"
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            # fallback: только дата
            dt = datetime.strptime(s, "%Y-%m-%d")
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise TypeError(f"Unsupported timestamp type: {type(value).__name__}")


def _build_copy_statement(
    table_name: str,
    start_ts: datetime | None,
    end_ts: datetime | None,
    columns: Sequence[str] | None,
) -> tuple[sql.Composed, tuple]:
    """Строит безопасный COPY-SQL через psycopg2.sql.* и параметры для WHERE.

    Возвращает (composed_sql, params). psycopg2 подставит params в литералы
    внутри COPY через copy_expert+cursor.mogrify.
    """
    if columns:
        cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    else:
        cols_sql = sql.SQL("*")

    where_parts: list[sql.Composable] = []
    params: list = []
    if start_ts is not None:
        where_parts.append(sql.SQL("timestamp_utc >= %s"))
        params.append(start_ts)
    if end_ts is not None:
        where_parts.append(sql.SQL("timestamp_utc <= %s"))
        params.append(end_ts)

    where_sql: sql.Composable
    if where_parts:
        where_sql = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where_parts)
    else:
        where_sql = sql.SQL("")

    inner = sql.SQL("SELECT {cols} FROM {tbl}{where} ORDER BY timestamp_utc").format(
        cols=cols_sql,
        tbl=sql.Identifier(table_name),
        where=where_sql,
    )
    copy_stmt = sql.SQL("COPY ({inner}) TO STDOUT WITH (FORMAT csv, HEADER true)").format(
        inner=inner
    )
    return copy_stmt, tuple(params)


def export_dataset_csv(
    conn: psycopg2.extensions.connection,
    table_name: str,
    start_ts_utc: _Timestamp = None,
    end_ts_utc: _Timestamp = None,
    columns: Sequence[str] | None = None,
) -> bytes:
    """Экспортирует диапазон таблицы датасета в CSV через `COPY ... TO STDOUT`.

    Параметры
    ----------
    conn           : открытое psycopg2-подключение.
    table_name     : имя таблицы в схеме public (например, ``btcusdt_5m``).
    start_ts_utc   : нижняя граница (inclusive) по ``timestamp_utc``. ``None``
                     → без ограничения снизу. Принимает ``datetime`` или ISO-строку.
    end_ts_utc     : верхняя граница (inclusive) по ``timestamp_utc``. ``None``
                     → без ограничения сверху.
    columns        : список колонок; ``None`` или пустой → ``*``.

    Возвращает
    ----------
    bytes : содержимое CSV (UTF-8), с заголовком, упорядоченное по
    ``timestamp_utc``. Память: один строковый буфер размером с CSV.
    """
    start_ts = _parse_ts(start_ts_utc)
    end_ts = _parse_ts(end_ts_utc)

    copy_stmt, params = _build_copy_statement(table_name, start_ts, end_ts, columns)

    buf = io.BytesIO()
    with conn.cursor() as cur:
        # mogrify использует C-биндинг cursor для quote_ident идентификаторов и
        # литералов параметров; возвращает bytes с полностью срендеренным SQL.
        rendered = cur.mogrify(copy_stmt, params).decode("utf-8")
        # copy_expert пишет bytes в file-like объект — BytesIO принимает их
        # напрямую без Python-уровневого encode/decode на каждом чанке.
        cur.copy_expert(rendered, buf)

    return buf.getvalue()  # уже bytes — encode не нужен
