"""Загрузка обучающих данных из PostgreSQL в pandas DataFrame."""
from __future__ import annotations

import pandas as pd
import psycopg2
from psycopg2 import sql

from backend.dataset.core import log

from .config import EXCLUDE_FROM_FEATURES, TARGET_COLUMN


def load_training_data(
    connection: psycopg2.extensions.connection,
    table_name: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    min_rows: int = 200,
) -> tuple[pd.DataFrame, pd.Series, list[str], pd.Series]:
    """Загружает строки таблицы, формирует матрицу признаков X и вектор целей y.

    Аргументы:
        date_from  — ISO-строка или None: нижняя граница timestamp_utc (включительно).
        date_to    — ISO-строка или None: верхняя граница timestamp_utc (включительно).

    Возвращает:
        X             — DataFrame числовых признаков (NaN сохраняются, CatBoost справится)
        y             — Series с target_return_1 (строки с NaN в target удалены)
        feature_cols  — список имён признаков (порядок соответствует колонкам X)
        timestamps    — Series с timestamp_utc (тот же индекс что у X/y, для графиков)
    """
    with connection.cursor() as cursor:
        conditions: list[str] = []
        params: list = []
        if date_from:
            conditions.append("timestamp_utc >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("timestamp_utc <= %s")
            params.append(date_to)

        if conditions:
            query = sql.SQL(
                "SELECT * FROM {} WHERE {} ORDER BY timestamp_utc"
            ).format(
                sql.Identifier(table_name),
                sql.SQL(" AND ").join(sql.SQL(c) for c in conditions),
            )
        else:
            query = sql.SQL(
                "SELECT * FROM {} ORDER BY timestamp_utc"
            ).format(sql.Identifier(table_name))

        cursor.execute(query, params or None)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

    if not rows:
        raise ValueError(f"Таблица {table_name!r} пуста — нет данных для обучения.")

    df = pd.DataFrame(rows, columns=columns)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    df = df.sort_values("timestamp_utc").reset_index(drop=True)

    log(f"[loader] Загружено {len(df)} строк из {table_name!r}, столбцов: {len(df.columns)}")

    # Определяем числовые признаки (исключаем мета-колонки и target)
    feature_cols = [
        col for col in df.columns
        if col not in EXCLUDE_FROM_FEATURES
        and pd.api.types.is_numeric_dtype(df[col])
    ]

    # Удаляем строки без target (последняя свеча всегда NaN из-за shift(-1))
    before = len(df)
    df = df.dropna(subset=[TARGET_COLUMN]).reset_index(drop=True)
    log(f"[loader] Удалено {before - len(df)} строк с NaN в {TARGET_COLUMN!r}")

    if len(df) < min_rows:
        raise ValueError(
            f"Недостаточно данных: {len(df)} строк после удаления NaN "
            f"(минимум {min_rows})."
        )

    X = df[feature_cols].copy()
    y = df[TARGET_COLUMN].copy()
    timestamps = df["timestamp_utc"].copy()

    log(f"[loader] X: {X.shape}, y: {y.shape}, признаков: {len(feature_cols)}")
    return X, y, feature_cols, timestamps
