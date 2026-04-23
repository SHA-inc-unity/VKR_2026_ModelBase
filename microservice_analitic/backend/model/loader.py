"""Загрузка обучающих данных из PostgreSQL в pandas DataFrame."""
from __future__ import annotations

import pandas as pd
import psycopg2
from psycopg2 import sql

from backend.dataset.core import log

from .config import META_COLUMNS, TARGET_COLUMN, TARGET_COLUMN_PREFIX


def list_target_candidates(
    connection: psycopg2.extensions.connection,
    table_name: str,
) -> list[str]:
    """Возвращает список колонок таблицы, имя которых начинается с TARGET_COLUMN_PREFIX.

    Используется UI для заполнения селектора «Целевая переменная» до загрузки данных.
    Пустой список означает, что таблица не содержит target_*-колонок.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = %s
               AND column_name LIKE %s
             ORDER BY column_name
            """,
            (table_name, f"{TARGET_COLUMN_PREFIX}%"),
        )
        return [row[0] for row in cursor.fetchall()]


def load_training_data(
    connection: psycopg2.extensions.connection,
    table_name: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    min_rows: int = 200,
    target_col: str | None = None,
) -> tuple[pd.DataFrame, pd.Series, list[str], pd.Series]:
    """Загружает строки таблицы, формирует матрицу признаков X и вектор целей y.

    Аргументы:
        date_from  — ISO-строка или None: нижняя граница timestamp_utc (включительно).
        date_to    — ISO-строка или None: верхняя граница timestamp_utc (включительно).
        target_col — имя колонки-цели; None → config.TARGET_COLUMN (target_return_1).

    Возвращает:
        X             — DataFrame числовых признаков (NaN сохраняются, CatBoost справится)
        y             — Series с значениями target_col (строки с NaN в target удалены)
        feature_cols  — список имён признаков (порядок соответствует колонкам X)
        timestamps    — Series с timestamp_utc (тот же индекс что у X/y, для графиков)
    """
    target = target_col or TARGET_COLUMN
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

    log(
        f"[loader] Загружено {len(df)} строк из {table_name!r}, столбцов: {len(df.columns)}, "
        f"target={target!r}"
    )

    if target not in df.columns:
        raise ValueError(
            f"Колонка-цель {target!r} отсутствует в таблице {table_name!r}. "
            f"Доступные target-колонки: "
            f"{[c for c in df.columns if c.startswith(TARGET_COLUMN_PREFIX)]}"
        )

    # Определяем числовые признаки: исключаем META_COLUMNS и ВСЕ target_*-колонки
    # (чтобы при наличии нескольких целевых горизонтов не протекали данные).
    excluded = META_COLUMNS | {
        c for c in df.columns if c.startswith(TARGET_COLUMN_PREFIX)
    }
    feature_cols = [
        col for col in df.columns
        if col not in excluded
        and pd.api.types.is_numeric_dtype(df[col])
    ]

    # Удаляем строки без target (последняя свеча всегда NaN из-за shift(-1))
    before = len(df)
    df = df.dropna(subset=[target]).reset_index(drop=True)
    log(f"[loader] Удалено {before - len(df)} строк с NaN в {target!r}")

    if len(df) < min_rows:
        raise ValueError(
            f"Недостаточно данных: {len(df)} строк после удаления NaN "
            f"(минимум {min_rows})."
        )

    # Валидация признаков: отбрасываем полностью NaN и константные,
    # предупреждаем о колонках с высокой долей пропусков.
    feature_cols = _validate_features(df, feature_cols)

    X = df[feature_cols].copy()
    y = df[target].copy()
    timestamps = df["timestamp_utc"].copy()

    log(f"[loader] X: {X.shape}, y: {y.shape}, признаков: {len(feature_cols)}")
    return X, y, feature_cols, timestamps


def _validate_features(df: pd.DataFrame, feature_cols: list[str]) -> list[str]:
    """Отбрасывает полностью пустые и константные признаки, предупреждает о высокой доле NaN.

    Реализовано векторизованно: вместо O(n × m) Python-цикла по колонкам с per-col
    вызовом .std()/.isna().mean() — два batched-вызова .std()/.isna().mean() на
    срез DataFrame, что даёт 5-20× ускорение для 50+ признаков и 2M строк.
    """
    n_rows = len(df)
    if n_rows == 0 or not feature_cols:
        return feature_cols

    sub = df[feature_cols]
    # Одним pandas-вызовом: std() по всем колонкам; NaN std → «всё NaN» колонка.
    stds = sub.std(axis=0, skipna=True, numeric_only=False)
    nan_frac = sub.isna().mean(axis=0)

    all_nan_mask = nan_frac >= 1.0
    # Константные — std == 0 (NaN std ловится через all_nan_mask выше).
    constant_mask = (~all_nan_mask) & (stds.fillna(0.0) == 0.0)
    high_nan_mask = (~all_nan_mask) & (~constant_mask) & (nan_frac > 0.30)
    keep_mask = ~(all_nan_mask | constant_mask)

    dropped_all_nan = stds.index[all_nan_mask].tolist()
    dropped_constant = stds.index[constant_mask].tolist()
    warn_high_nan = [(c, float(nan_frac[c])) for c in stds.index[high_nan_mask]]
    kept = stds.index[keep_mask].tolist()

    if dropped_all_nan:
        log(
            f"[loader] Отброшено {len(dropped_all_nan)} полностью NaN признаков: "
            f"{dropped_all_nan}"
        )
    if dropped_constant:
        log(
            f"[loader] Отброшено {len(dropped_constant)} константных признаков "
            f"(std=0 или NaN): {dropped_constant}"
        )
    if warn_high_nan:
        preview = ", ".join(f"{c}={f:.1%}" for c, f in warn_high_nan[:10])
        more = f" (+{len(warn_high_nan) - 10} ещё)" if len(warn_high_nan) > 10 else ""
        log(f"[loader] WARN: {len(warn_high_nan)} признаков с NaN > 30%: {preview}{more}")

    return kept
