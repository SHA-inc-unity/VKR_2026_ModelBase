"""In-memory dataset cache keyed by (symbol, timeframe, start_date, end_date).

Mirrors the PostgreSQL data model:
  - table  : {symbol}_{timeframe}   (e.g. btcusdt_5m)
  - PK     : timestamp_utc

The cache avoids repeated DB round-trips for the same symbol/timeframe/range
within a single process lifetime (survives Streamlit reruns).

OOM protection
--------------
Cache enforces both an entry-count limit *and* a byte-budget limit.
Each entry's size is estimated as ``len(rows) * _BYTES_PER_ROW_ESTIMATE``.
Oldest entries are evicted (FIFO) until both constraints are satisfied.
A single entry larger than max_bytes is never stored (returned uncached).

If psutil is available, the cache also checks available system RAM before
storing: when free RAM drops below ``_OOM_GUARD_MIN_AVAIL_BYTES`` the entry
is returned to the caller without being stored.

Typical usage
-------------
    from backend.dataset.dataset_cache import dataset_cache

    rows = dataset_cache.get(conn, "btcusdt", "5m", start_ms, end_ms)

    # or with an explicit DatasetCache instance (e.g. in tests):
    cache = DatasetCache(max_entries=5)
    rows = cache.get(conn, "btcusdt", "5m", start_ms, end_ms)
"""
from __future__ import annotations

import gc
from typing import TYPE_CHECKING

from .core import make_table_name, ms_to_datetime
from .database import fetch_db_rows

if TYPE_CHECKING:
    import psycopg2.extensions

# ── psutil (optional) ─────────────────────────────────────────────────────────
try:
    import psutil as _psutil  # type: ignore[import]
    _PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSUTIL_AVAILABLE = False

# ── byte-budget constants ─────────────────────────────────────────────────────
# Rough estimate: 51 columns × ~60 bytes Python-object overhead per cell
_BYTES_PER_ROW_ESTIMATE: int = 3_000

# Minimum free system RAM to allow caching (100 MB)
_OOM_GUARD_MIN_AVAIL_BYTES: int = 100 * 1024 * 1024

# ─────────────────────────────────────────────────────────────────────────────
# Тип ключа: (symbol, timeframe, start_date_iso, end_date_iso)
# Примеры: ("btcusdt", "5m", "2024-01-01", "2024-12-31")
#           ("ethusdt", "60m", "2025-03-01", "2025-04-01")
# ─────────────────────────────────────────────────────────────────────────────
CacheKey = tuple[str, str, str, str]


class DatasetCache:
    """Кэш датасетов в памяти Python.

    Ключ  : (symbol, timeframe, start_date_iso, end_date_iso)
    Значение : dict[int, dict] — {timestamp_ms: row_dict}
               (формат, который возвращает fetch_db_rows)

    Параметры
    ----------
    max_entries : int
        Максимальное число записей в кэше.
    max_bytes : int
        Максимальный суммарный объём данных в кэше (в байтах).
        Оценка: len(rows) × _BYTES_PER_ROW_ESTIMATE.
        По умолчанию 256 МБ.

    При превышении любого из лимитов вытесняется самая старая запись
    (FIFO, порядок вставки сохраняется в dict).
    """

    def __init__(
        self,
        max_entries: int = 10,
        max_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        if max_bytes < 1:
            raise ValueError("max_bytes must be >= 1")
        self._max = max_entries
        self._max_bytes = max_bytes
        # Упорядоченный словарь (insertion-order в CPython 3.7+)
        self._data: dict[CacheKey, dict[int, dict]] = {}
        # Byte-budget tracking per entry
        self._entry_bytes: dict[CacheKey, int] = {}
        self._total_bytes: int = 0

    # ── Основной метод ────────────────────────────────────────────────────────

    def get(
        self,
        conn: psycopg2.extensions.connection,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
    ) -> dict[int, dict]:
        """Вернуть строки из кэша или загрузить из PostgreSQL.

        Parameters
        ----------
        conn      : Активное psycopg2-соединение (используется только при cache miss).
        symbol    : Символ инструмента, напр. «btcusdt».
        timeframe : Таймфрейм, напр. «5m», «60m».
        start_ms  : Начало диапазона в мс UTC (включительно).
        end_ms    : Конец диапазона в мс UTC (включительно).

        Returns
        -------
        dict[int, dict]
            Словарь {timestamp_ms: row_dict}.
        """
        key = self.make_key(symbol, timeframe, start_ms, end_ms)
        if key in self._data:
            return self._data[key]

        # Cache miss — читаем из PG
        table_name = make_table_name(symbol, timeframe)
        try:
            rows = fetch_db_rows(conn, table_name, start_ms, end_ms)
        except MemoryError:
            # При нехватке RAM очищаем кэш и пробрасываем ошибку выше
            self.clear()
            gc.collect()
            raise

        estimated_bytes = len(rows) * _BYTES_PER_ROW_ESTIMATE

        # Системная защита: если свободной RAM мало — не кэшируем
        if _PSUTIL_AVAILABLE:
            avail = _psutil.virtual_memory().available
            if avail < _OOM_GUARD_MIN_AVAIL_BYTES:
                gc.collect()
                avail = _psutil.virtual_memory().available
                if avail < _OOM_GUARD_MIN_AVAIL_BYTES:
                    return rows  # некэшируем, просто возвращаем

        self._store(key, rows, estimated_bytes)
        return rows

    # ── Ручное управление ─────────────────────────────────────────────────────

    def put(
        self,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
        rows: dict[int, dict],
    ) -> None:
        """Положить данные в кэш напрямую (без обращения к БД)."""
        key = self.make_key(symbol, timeframe, start_ms, end_ms)
        self._store(key, rows)

    def contains(
        self,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
    ) -> bool:
        """True если данные уже есть в кэше (без запроса к БД)."""
        return self.make_key(symbol, timeframe, start_ms, end_ms) in self._data

    def invalidate(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> int:
        """Удалить записи по фильтру.

        Parameters
        ----------
        symbol    : None → любой символ.
        timeframe : None → любой таймфрейм.

        Returns
        -------
        int : число удалённых записей.
        """
        if symbol is None and timeframe is None:
            n = len(self._data)
            self.clear()
            return n

        to_remove = [
            k for k in self._data
            if (symbol is None or k[0] == symbol.lower())
            and (timeframe is None or k[1] == timeframe.lower())
        ]
        for k in to_remove:
            self._total_bytes -= self._entry_bytes.pop(k, 0)
            del self._data[k]
        return len(to_remove)

    def clear(self) -> None:
        """Полностью очистить кэш."""
        self._data.clear()
        self._entry_bytes.clear()
        self._total_bytes = 0

    # ── Свойства/служебные ────────────────────────────────────────────────────

    @property
    def keys(self) -> list[CacheKey]:
        """Все текущие ключи в порядке вставки."""
        return list(self._data.keys())

    @property
    def memory_usage_bytes(self) -> int:
        """Оценочный суммарный объём кэша в байтах."""
        return self._total_bytes

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: CacheKey) -> bool:
        return key in self._data

    def __repr__(self) -> str:
        mb_used = self._total_bytes // 1024 // 1024
        mb_max = self._max_bytes // 1024 // 1024
        return (
            f"DatasetCache(entries={len(self._data)}/{self._max}, "
            f"bytes={mb_used}MB/{mb_max}MB, "
            f"keys={self.keys!r})"
        )

    # ── Вспомогательные ───────────────────────────────────────────────────────

    @staticmethod
    def make_key(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> CacheKey:
        """Построить ключ кэша из параметров запроса.

        Ключ: (symbol_lower, timeframe_lower, start_date_iso, end_date_iso)

        Пример: ("btcusdt", "5m", "2024-01-01", "2024-12-31")
        Это зеркалит Postgres: таблица btcusdt_5m, PK timestamp_utc.
        """
        start_iso = ms_to_datetime(start_ms).date().isoformat()
        end_iso = ms_to_datetime(end_ms).date().isoformat()
        return (symbol.lower(), timeframe.lower(), start_iso, end_iso)

    def _store(
        self,
        key: CacheKey,
        rows: dict[int, dict],
        estimated_bytes: int | None = None,
    ) -> None:
        """Поместить запись в кэш, соблюдая оба лимита (count + bytes).

        Одиночная запись, превышающая max_bytes, не сохраняется.
        """
        if estimated_bytes is None:
            estimated_bytes = len(rows) * _BYTES_PER_ROW_ESTIMATE

        # Слишком большая запись — не кэшируем совсем
        if estimated_bytes > self._max_bytes:
            return

        # Если ключ уже есть — снимаем его старый размер и удаляем
        if key in self._data:
            self._total_bytes -= self._entry_bytes.pop(key, 0)
            del self._data[key]

        # Вытесняем самую старую запись, пока оба лимита нарушены
        while self._data and (
            len(self._data) >= self._max
            or self._total_bytes + estimated_bytes > self._max_bytes
        ):
            oldest_key = next(iter(self._data))
            self._total_bytes -= self._entry_bytes.pop(oldest_key, 0)
            del self._data[oldest_key]

        self._data[key] = rows
        self._entry_bytes[key] = estimated_bytes
        self._total_bytes += estimated_bytes


# ─────────────────────────────────────────────────────────────────────────────
# Глобальный синглтон — выживает между reruns Streamlit и сессиями модуля.
# Максимум 10 записей (≈ 10 уникальных диапазонов).
# ─────────────────────────────────────────────────────────────────────────────
dataset_cache: DatasetCache = DatasetCache(max_entries=10)
