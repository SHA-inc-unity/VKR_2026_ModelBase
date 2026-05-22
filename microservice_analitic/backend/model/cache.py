"""Персистентный кеш обучающих датасетов (parquet + JSON-метаданные).

Цель — исключить повторные тяжёлые SELECT * из PostgreSQL при работе с одним
и тем же (symbol, timeframe, диапазон дат, target) между перезагрузками Streamlit.

Ключ кеша формируется из (table_name, date_from, date_to, target_col) через SHA-256.
Хранение: MODELS_DIR/cache/{key}.parquet (данные) + {key}.meta.json (метаданные).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pandas as pd

from backend.dataset.core import log

from .config import MODELS_DIR, TARGET_COLUMN

CACHE_DIR: Path = MODELS_DIR / "cache"


def _feature_cols_hash(feature_cols: "list[str] | None") -> str:
    """SHA-256[:8] отсортированного списка признаков — для детекции изменения схемы таблицы."""
    if not feature_cols:
        return ""
    return hashlib.sha256("|".join(sorted(feature_cols)).encode("utf-8")).hexdigest()[:8]


def _cache_key(
    table_name: str,
    date_from: "str | None",
    date_to: "str | None",
    target_col: "str | None",
) -> str:
    payload = "|".join([
        table_name or "",
        date_from or "",
        date_to or "",
        target_col or TARGET_COLUMN,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _paths(key: str, cache_dir: Path) -> tuple[Path, Path]:
    return cache_dir / f"{key}.parquet", cache_dir / f"{key}.meta.json"


def load_cached_dataset(
    table_name: str,
    *,
    date_from: "str | None" = None,
    date_to: "str | None" = None,
    target_col: "str | None" = None,
    cache_dir: Path = CACHE_DIR,
    max_age_s: "float | None" = None,
    expected_feature_cols: "list[str] | None" = None,
) -> "tuple[pd.DataFrame, pd.Series, list[str], pd.Series] | None":
    """Возвращает (X, y, feature_cols, timestamps) из кеша или None при промахе.

    max_age_s            — опциональный TTL в секундах.
    expected_feature_cols — если передан, кеш инвалидируется при изменении набора признаков
                            (детектирует добавление/удаление колонок в PostgreSQL-таблице).
    """
    key = _cache_key(table_name, date_from, date_to, target_col)
    parq, meta = _paths(key, cache_dir)
    if not (parq.exists() and meta.exists()):
        return None
    try:
        md = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None
    if max_age_s is not None:
        cached_at = float(md.get("cached_at", 0.0))
        age = time.time() - cached_at
        if age > max_age_s:
            log(f"[cache] MISS {table_name}: возраст {age / 60:.1f} мин > TTL {max_age_s / 60:.1f}")
            return None
    # Проверка совместимости схемы таблицы
    if expected_feature_cols is not None:
        current_hash = _feature_cols_hash(expected_feature_cols)
        saved_hash   = md.get("feature_cols_hash", "")
        if current_hash != saved_hash:
            log(
                f"[cache] MISS {table_name}: схема таблицы изменилась "
                f"(saved={saved_hash!r} current={current_hash!r}) — инвалидация"
            )
            return None
    try:
        df = pd.read_parquet(parq)
    except Exception:
        return None

    target = md.get("target_col") or TARGET_COLUMN
    feature_cols: list[str] = list(md.get("feature_cols") or [])
    if target not in df.columns or "timestamp_utc" not in df.columns:
        return None
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        log(f"[cache] MISS {table_name}: в parquet отсутствуют колонки {missing[:5]}")
        return None

    X = df[feature_cols].copy()
    y = df[target].copy()
    ts = df["timestamp_utc"].copy()
    log(f"[cache] HIT {table_name}: {len(df):,} строк, признаков={len(feature_cols)}, target={target!r}")
    return X, y, feature_cols, ts


def save_cached_dataset(
    X: pd.DataFrame,
    y: pd.Series,
    feature_cols: list[str],
    timestamps: pd.Series,
    *,
    table_name: str,
    date_from: "str | None" = None,
    date_to: "str | None" = None,
    target_col: "str | None" = None,
    cache_dir: Path = CACHE_DIR,
) -> Path:
    """Сохраняет датасет в {key}.parquet и {key}.meta.json. Возвращает путь к parquet."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(table_name, date_from, date_to, target_col)
    parq, meta = _paths(key, cache_dir)

    target = target_col or TARGET_COLUMN
    df = X.copy()
    df["timestamp_utc"] = timestamps.values
    df[target] = y.values
    df.to_parquet(parq, index=False)

    meta.write_text(
        json.dumps({
            "table_name":        table_name,
            "date_from":         date_from,
            "date_to":           date_to,
            "target_col":        target,
            "feature_cols":      list(feature_cols),
            "feature_cols_hash": _feature_cols_hash(list(feature_cols)),
            "n_rows":            len(df),
            "cached_at":         time.time(),
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log(
        f"[cache] SAVE {table_name} → {parq.name}  "
        f"{len(df):,} строк, target={target!r}, признаков={len(feature_cols)}"
    )
    return parq


def clear_cache(cache_dir: Path = CACHE_DIR) -> int:
    """Удаляет все файлы кеша. Возвращает количество удалённых файлов."""
    if not cache_dir.exists():
        return 0
    n = 0
    for p in list(cache_dir.glob("*.parquet")) + list(cache_dir.glob("*.meta.json")):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    log(f"[cache] CLEAR: удалено {n} файлов")
    return n


def cache_stats(cache_dir: Path = CACHE_DIR) -> dict:
    """Сводка по кешу: n_files, total_bytes, entries=[{file, table, target, n_rows, cached_at, bytes}, ...]."""
    if not cache_dir.exists():
        return {"n_files": 0, "total_bytes": 0, "entries": []}
    entries: list[dict] = []
    total = 0
    for p in cache_dir.glob("*.parquet"):
        meta_path = p.with_suffix(".meta.json")
        size = p.stat().st_size
        total += size
        md: dict = {}
        if meta_path.exists():
            try:
                md = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                md = {}
        entries.append({
            "file":      p.name,
            "table":     md.get("table_name"),
            "target":    md.get("target_col"),
            "n_rows":    md.get("n_rows"),
            "cached_at": md.get("cached_at"),
            "bytes":     size,
        })
    return {"n_files": len(entries), "total_bytes": total, "entries": entries}
