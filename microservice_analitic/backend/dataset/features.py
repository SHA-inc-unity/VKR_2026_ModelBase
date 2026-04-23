from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

from .constants import (
    DEFAULT_WARMUP_CANDLES,
    FUNDING_LAG_STEPS,
    LAG_STEPS,
    OI_LAG_STEPS,
    RAW_FEATURE_COLUMNS,
    RETURN_HORIZONS,
    ROLLING_WINDOWS,
    RSI_LAG_STEPS,
    TARGET_HORIZON_MS,
    TIMEFRAMES,
)


def _infer_step_ms(g: pd.DataFrame) -> int:
    """Возвращает шаг таймфрейма в мс для группы.

    Сначала пытается прочитать из столбца 'timeframe' (точный справочник),
    затем — оценить по медиане разностей временных меток.
    """
    if "timeframe" in g.columns and not g.empty:
        tf = str(g["timeframe"].iloc[0])
        if tf in TIMEFRAMES:
            return int(TIMEFRAMES[tf][1])
    if len(g) >= 2:
        ts = g["timestamp_utc"]
        if pd.api.types.is_datetime64_any_dtype(ts):
            return max(1, int(ts.diff().median().total_seconds() * 1000))
    return 3_600_000  # умолчание: 1 час


def _compute_group_features(
    g: pd.DataFrame,
    add_target: bool,
    target_horizon_ms: int = TARGET_HORIZON_MS,
) -> pd.DataFrame:
    """Вычисляет все признаки для одной пары (symbol, timeframe)."""
    g = g.copy().sort_values("timestamp_utc").reset_index(drop=True)
    price: pd.Series = g["index_price"]

    for lag in LAG_STEPS:
        g[f"price_lag_{lag}"] = price.shift(lag)

    for horizon in RETURN_HORIZONS:
        g[f"return_{horizon}"] = price.pct_change(horizon)
        prev = price.shift(horizon).replace(0.0, np.nan)
        ratio = (price / prev).clip(lower=1e-10)
        g[f"log_return_{horizon}"] = np.log(ratio)

    for window in ROLLING_WINDOWS:
        roll = price.rolling(window, min_periods=1)
        mean = roll.mean()
        std = roll.std(ddof=0)
        g[f"price_roll{window}_mean"] = mean
        g[f"price_roll{window}_std"] = std
        g[f"price_roll{window}_min"] = roll.min()
        g[f"price_roll{window}_max"] = roll.max()
        safe_mean = mean.replace(0.0, np.nan)
        g[f"price_to_roll{window}_mean"] = price / safe_mean
        g[f"price_vol_{window}"] = std / safe_mean

    if "funding_rate" in g.columns:
        funding_rate = g["funding_rate"].ffill()
        for lag in FUNDING_LAG_STEPS:
            g[f"funding_lag_{lag}"] = funding_rate.shift(lag)
        for window in ROLLING_WINDOWS:
            g[f"funding_roll{window}_mean"] = funding_rate.rolling(window, min_periods=1).mean()

    if "open_interest" in g.columns:
        open_interest = g["open_interest"].ffill()
        for lag in OI_LAG_STEPS:
            g[f"oi_lag_{lag}"] = open_interest.shift(lag)
        for window in ROLLING_WINDOWS:
            g[f"oi_roll{window}_mean"] = open_interest.rolling(window, min_periods=1).mean()
        g["oi_return_1"] = open_interest.pct_change(1)

    if "rsi" in g.columns:
        for lag in RSI_LAG_STEPS:
            g[f"rsi_lag_{lag}"] = g["rsi"].shift(lag)

    timestamps = g["timestamp_utc"]
    if timestamps.dt.tz is None:
        timestamps = timestamps.dt.tz_localize("UTC")
    hour = timestamps.dt.hour.astype(float)
    dow = timestamps.dt.dayofweek.astype(float)
    g["hour_sin"] = np.sin(2.0 * np.pi * hour / 24.0)
    g["hour_cos"] = np.cos(2.0 * np.pi * hour / 24.0)
    g["dow_sin"] = np.sin(2.0 * np.pi * dow / 7.0)
    g["dow_cos"] = np.cos(2.0 * np.pi * dow / 7.0)

    if "open_interest" in g.columns and "funding_rate" in g.columns:
        open_interest = g["open_interest"].ffill()
        funding_rate = g["funding_rate"].ffill().replace(0.0, np.nan)
        g["oi_to_funding"] = open_interest / funding_rate

    if add_target:
        step_ms = _infer_step_ms(g)
        # Число баров, соответствующих горизонту прогноза (3 часа по умолчанию).
        # Для таймфреймов ≥3ч используем 1 бар — нельзя предсказывать дальше шага.
        bars = max(1, round(target_horizon_ms / step_ms))
        g["target_return_1"] = price.pct_change(bars).shift(-bars)

    return g


def build_features(
    df: pd.DataFrame,
    add_target: bool = True,
    warmup_candles: int = DEFAULT_WARMUP_CANDLES,
    target_horizon_ms: int = TARGET_HORIZON_MS,
) -> pd.DataFrame:
    """Строит матрицу признаков из сырого датасета рыночных данных.

    Параметр target_horizon_ms задаёт горизонт прогноза в миллисекундах.
    По умолчанию — 3 часа (TARGET_HORIZON_MS = 10_800_000 мс).
    Число баров N вычисляется как max(1, round(target_horizon_ms / step_ms))
    для каждой группы (symbol, timeframe) отдельно.

    Если групп > 1, вычисление признаков ведётся параллельно —
    до min(n_groups, cpu_count) воркеров. Каждая группа независима,
    поэтому параллелизм прямолинеен.
    """
    required = {"timestamp_utc", "index_price"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        raise ValueError(f"Недостающие обязательные столбцы: {missing_cols}")

    df = df.copy()
    group_cols = [column_name for column_name in ("symbol", "timeframe") if column_name in df.columns]

    if group_cols:
        groups = list(df.groupby(group_cols, sort=False))
        n_workers = min(len(groups), max(1, (math.ceil(len(groups) / 2))))
        if n_workers > 1:
            parts: list[pd.DataFrame] = [None] * len(groups)  # type: ignore[list-item]
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futures = {
                    pool.submit(_compute_group_features, grp, add_target, target_horizon_ms): idx
                    for idx, (_, grp) in enumerate(groups)
                }
                for fut in as_completed(futures):
                    parts[futures[fut]] = fut.result()
        else:
            parts = [_compute_group_features(grp, add_target, target_horizon_ms) for _, grp in groups]
        result = pd.concat(parts, ignore_index=True)
    else:
        result = _compute_group_features(
            df.sort_values("timestamp_utc").reset_index(drop=True),
            add_target,
            target_horizon_ms,
        )

    sort_by = [*group_cols, "timestamp_utc"]
    result = result.sort_values(sort_by).reset_index(drop=True)

    if warmup_candles > 0:
        if group_cols:
            # Быстрая векторизованная обрезка warmup-строк на группу:
            # groupby.cumcount() присваивает каждой строке её порядковый номер
            # внутри группы → маска cumcount >= warmup фильтрует в один проход,
            # без Python-цикла по группам и без pd.concat из списков (≈10× быстрее
            # для многогрупповых датасетов).
            cc = result.groupby(group_cols, sort=False).cumcount()
            result = result.loc[cc >= warmup_candles].reset_index(drop=True)
        else:
            result = result.iloc[warmup_candles:].reset_index(drop=True)

    return result


def get_feature_columns(df: pd.DataFrame) -> list[str]:
    """Возвращает список имён признаков (без исходных и целевого столбца)."""
    return [column_name for column_name in df.columns if column_name not in RAW_FEATURE_COLUMNS]


def prepare_for_catboost(
    df: pd.DataFrame,
    drop_na_target: bool = True,
) -> tuple[pd.DataFrame, pd.Series | None]:
    """Разделяет DataFrame признаков на X (матрица) и y (цель)."""
    feature_cols = get_feature_columns(df)
    if not feature_cols:
        raise ValueError("Столбцы признаков не найдены. Сначала вызовите build_features().")
    if drop_na_target and "target_return_1" in df.columns:
        df = df.dropna(subset=["target_return_1"]).reset_index(drop=True)
    target = df["target_return_1"].copy() if "target_return_1" in df.columns else None
    features = df[feature_cols].copy()
    return features, target
