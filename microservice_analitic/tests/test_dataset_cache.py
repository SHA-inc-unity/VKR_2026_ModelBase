"""Tests for backend.dataset.dataset_cache.DatasetCache."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.dataset.dataset_cache import CacheKey, DatasetCache


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────────────────────────────────

def _ms(year: int, month: int = 1, day: int = 1) -> int:
    """Переводит дату в миллисекунды UTC."""
    dt = datetime(year, month, day, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _rows(n: int = 3) -> dict[int, dict]:
    """Создаёт фиктивный словарь строк {ts_ms: {col: val}}."""
    base = _ms(2024, 1, 1)
    step = 300_000  # 5m
    return {base + i * step: {"close": float(i), "open": float(i) + 0.5} for i in range(n)}


def _make_conn(rows: dict[int, dict]) -> MagicMock:
    """Мок psycopg2-соединения, fetch_db_rows вернёт заданный rows."""
    return MagicMock()  # реальный return подставляем через patch


# ─────────────────────────────────────────────────────────────────────────────
# make_key
# ─────────────────────────────────────────────────────────────────────────────

def test_make_key_structure():
    key = DatasetCache.make_key("BTCUSDT", "5M", _ms(2024, 1, 1), _ms(2024, 12, 31))
    assert key == ("btcusdt", "5m", "2024-01-01", "2024-12-31")


def test_make_key_is_stable():
    k1 = DatasetCache.make_key("btcusdt", "5m", _ms(2024, 3, 1), _ms(2024, 3, 31))
    k2 = DatasetCache.make_key("btcusdt", "5m", _ms(2024, 3, 1), _ms(2024, 3, 31))
    assert k1 == k2


def test_make_key_differs_by_symbol():
    k_btc = DatasetCache.make_key("btcusdt", "5m", _ms(2024), _ms(2024, 6))
    k_eth = DatasetCache.make_key("ethusdt", "5m", _ms(2024), _ms(2024, 6))
    assert k_btc != k_eth


def test_make_key_differs_by_timeframe():
    k1 = DatasetCache.make_key("btcusdt", "5m",  _ms(2024), _ms(2024, 6))
    k2 = DatasetCache.make_key("btcusdt", "60m", _ms(2024), _ms(2024, 6))
    assert k1 != k2


def test_make_key_differs_by_date():
    k1 = DatasetCache.make_key("btcusdt", "5m", _ms(2024, 1), _ms(2024, 6))
    k2 = DatasetCache.make_key("btcusdt", "5m", _ms(2024, 2), _ms(2024, 6))
    assert k1 != k2


# ─────────────────────────────────────────────────────────────────────────────
# put / contains
# ─────────────────────────────────────────────────────────────────────────────

def test_put_and_contains():
    cache = DatasetCache(max_entries=5)
    rows = _rows()
    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), rows)
    assert cache.contains("btcusdt", "5m", _ms(2024), _ms(2024, 6))


def test_contains_false_on_empty():
    cache = DatasetCache()
    assert not cache.contains("btcusdt", "5m", _ms(2024), _ms(2024, 6))


def test_put_returns_correct_data():
    cache = DatasetCache()
    rows = _rows(5)
    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), rows)
    # Проверяем через get без DB (данные уже в кэше — conn не вызывается)
    conn = MagicMock()
    result = cache.get(conn, "btcusdt", "5m", _ms(2024), _ms(2024, 6))
    assert result is rows
    conn.cursor.assert_not_called()  # БД не трогали


# ─────────────────────────────────────────────────────────────────────────────
# get — cache miss → вызов fetch_db_rows
# ─────────────────────────────────────────────────────────────────────────────

def test_get_calls_db_on_miss():
    cache = DatasetCache()
    rows = _rows(4)
    conn = MagicMock()
    with patch("backend.dataset.dataset_cache.fetch_db_rows", return_value=rows) as mock_fetch:
        result = cache.get(conn, "btcusdt", "5m", _ms(2024), _ms(2024, 6))
    mock_fetch.assert_called_once_with(conn, "btcusdt_5m", _ms(2024), _ms(2024, 6))
    assert result is rows


def test_get_does_not_call_db_on_hit():
    cache = DatasetCache()
    rows = _rows()
    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), rows)
    conn = MagicMock()
    with patch("backend.dataset.dataset_cache.fetch_db_rows") as mock_fetch:
        result = cache.get(conn, "btcusdt", "5m", _ms(2024), _ms(2024, 6))
    mock_fetch.assert_not_called()
    assert result is rows


def test_get_stores_result_after_miss():
    cache = DatasetCache()
    rows = _rows()
    conn = MagicMock()
    with patch("backend.dataset.dataset_cache.fetch_db_rows", return_value=rows):
        cache.get(conn, "btcusdt", "5m", _ms(2024), _ms(2024, 6))
    assert cache.contains("btcusdt", "5m", _ms(2024), _ms(2024, 6))
    assert len(cache) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Eviction (FIFO при заполнении)
# ─────────────────────────────────────────────────────────────────────────────

def test_eviction_when_full():
    cache = DatasetCache(max_entries=3)
    for i in range(3):
        cache.put("btcusdt", "5m", _ms(2024 + i), _ms(2024 + i, 6), _rows())
    assert len(cache) == 3
    # Добавляем 4-й — вытесняется самый старый (2024)
    cache.put("btcusdt", "5m", _ms(2027), _ms(2027, 6), _rows())
    assert len(cache) == 3
    assert not cache.contains("btcusdt", "5m", _ms(2024), _ms(2024, 6))
    assert cache.contains("btcusdt", "5m", _ms(2027), _ms(2027, 6))


def test_put_same_key_does_not_grow():
    cache = DatasetCache(max_entries=3)
    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), _rows(2))
    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), _rows(5))  # обновление
    assert len(cache) == 1


# ─────────────────────────────────────────────────────────────────────────────
# invalidate / clear
# ─────────────────────────────────────────────────────────────────────────────

def test_invalidate_by_symbol():
    cache = DatasetCache()
    cache.put("btcusdt", "5m",  _ms(2024), _ms(2024, 6), _rows())
    cache.put("btcusdt", "60m", _ms(2024), _ms(2024, 6), _rows())
    cache.put("ethusdt", "5m",  _ms(2024), _ms(2024, 6), _rows())
    removed = cache.invalidate(symbol="btcusdt")
    assert removed == 2
    assert len(cache) == 1
    assert cache.contains("ethusdt", "5m", _ms(2024), _ms(2024, 6))


def test_invalidate_by_timeframe():
    cache = DatasetCache()
    cache.put("btcusdt", "5m",  _ms(2024), _ms(2024, 6), _rows())
    cache.put("ethusdt", "5m",  _ms(2024), _ms(2024, 6), _rows())
    cache.put("btcusdt", "60m", _ms(2024), _ms(2024, 6), _rows())
    removed = cache.invalidate(timeframe="5m")
    assert removed == 2
    assert cache.contains("btcusdt", "60m", _ms(2024), _ms(2024, 6))


def test_invalidate_all():
    cache = DatasetCache()
    for sym in ("btcusdt", "ethusdt"):
        cache.put(sym, "5m", _ms(2024), _ms(2024, 6), _rows())
    removed = cache.invalidate()
    assert removed == 2
    assert len(cache) == 0


def test_clear():
    cache = DatasetCache()
    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), _rows())
    cache.clear()
    assert len(cache) == 0


# ─────────────────────────────────────────────────────────────────────────────
# keys / __repr__ / __len__ / __contains__
# ─────────────────────────────────────────────────────────────────────────────

def test_keys_returns_list():
    cache = DatasetCache()
    k = DatasetCache.make_key("btcusdt", "5m", _ms(2024), _ms(2024, 6))
    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), _rows())
    assert cache.keys == [k]


def test_repr_contains_entry_count():
    cache = DatasetCache(max_entries=5)
    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), _rows())
    assert "1/5" in repr(cache)


def test_contains_dunder():
    cache = DatasetCache()
    k = DatasetCache.make_key("btcusdt", "5m", _ms(2024), _ms(2024, 6))
    assert k not in cache
    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), _rows())
    assert k in cache


# ─────────────────────────────────────────────────────────────────────────────
# Валидация конструктора
# ─────────────────────────────────────────────────────────────────────────────

def test_max_entries_zero_raises():
    with pytest.raises(ValueError):
        DatasetCache(max_entries=0)


def test_max_bytes_zero_raises():
    with pytest.raises(ValueError):
        DatasetCache(max_bytes=0)


# ─────────────────────────────────────────────────────────────────────────────
# OOM-защита: byte budget
# ─────────────────────────────────────────────────────────────────────────────

def _big_rows(n: int) -> dict[int, dict]:
    """Строки с именно n записями, каждая с многими полями."""
    base = _ms(2024, 1, 1)
    step = 300_000
    return {base + i * step: {f"col_{j}": float(i + j) for j in range(50)} for i in range(n)}


def test_byte_limit_evicts_oldest():
    """Хранить только записи, укладывающиеся в byte budget."""
    from backend.dataset.dataset_cache import _BYTES_PER_ROW_ESTIMATE

    rows_10 = _big_rows(10)
    entry_size = 10 * _BYTES_PER_ROW_ESTIMATE  # размер одной записи

    # budget вмещает ровно 2 записи
    cache = DatasetCache(max_entries=10, max_bytes=entry_size * 2)

    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), rows_10)   # запись A
    cache.put("btcusdt", "5m", _ms(2025), _ms(2025, 6), rows_10)   # запись B
    cache.put("btcusdt", "5m", _ms(2026), _ms(2026, 6), rows_10)   # запись C → вытесняет A

    assert not cache.contains("btcusdt", "5m", _ms(2024), _ms(2024, 6))
    assert cache.contains("btcusdt", "5m",  _ms(2025), _ms(2025, 6))
    assert cache.contains("btcusdt", "5m",  _ms(2026), _ms(2026, 6))
    assert len(cache) == 2


def test_entry_exceeding_budget_not_cached():
    """Запись, превышающая весь бюджет, не кэшируется."""
    from backend.dataset.dataset_cache import _BYTES_PER_ROW_ESTIMATE

    rows_huge = _big_rows(100)
    entry_size = 100 * _BYTES_PER_ROW_ESTIMATE
    # budget меньше одной записи
    cache = DatasetCache(max_entries=10, max_bytes=entry_size - 1)

    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), rows_huge)
    assert len(cache) == 0
    assert not cache.contains("btcusdt", "5m", _ms(2024), _ms(2024, 6))


def test_memory_usage_bytes_tracked():
    """memory_usage_bytes растёт при put и уменьшается при clear."""
    from backend.dataset.dataset_cache import _BYTES_PER_ROW_ESTIMATE

    cache = DatasetCache(max_entries=5)
    assert cache.memory_usage_bytes == 0

    rows_10 = _big_rows(10)
    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), rows_10)
    expected = 10 * _BYTES_PER_ROW_ESTIMATE
    assert cache.memory_usage_bytes == expected

    cache.clear()
    assert cache.memory_usage_bytes == 0


def test_memory_usage_bytes_decreases_on_invalidate():
    """Invalidate уменьшает memory_usage_bytes."""
    from backend.dataset.dataset_cache import _BYTES_PER_ROW_ESTIMATE

    cache = DatasetCache(max_entries=5)
    rows_10 = _big_rows(10)
    cache.put("btcusdt", "5m",  _ms(2024), _ms(2024, 6), rows_10)
    cache.put("ethusdt", "5m",  _ms(2024), _ms(2024, 6), rows_10)

    before = cache.memory_usage_bytes
    cache.invalidate(symbol="btcusdt")
    assert cache.memory_usage_bytes == before - 10 * _BYTES_PER_ROW_ESTIMATE


def test_update_same_key_corrects_byte_usage():
    """Обновление существующей записи не увеличивает _total_bytes дважды."""
    from backend.dataset.dataset_cache import _BYTES_PER_ROW_ESTIMATE

    cache = DatasetCache(max_entries=5)
    rows_5  = _big_rows(5)
    rows_10 = _big_rows(10)

    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), rows_5)
    assert cache.memory_usage_bytes == 5 * _BYTES_PER_ROW_ESTIMATE

    cache.put("btcusdt", "5m", _ms(2024), _ms(2024, 6), rows_10)
    assert cache.memory_usage_bytes == 10 * _BYTES_PER_ROW_ESTIMATE
    assert len(cache) == 1


def test_get_skips_cache_on_memory_error():
    """MemoryError в fetch_db_rows очищает кэш и пробрасывается дальше."""
    cache = DatasetCache(max_entries=5)
    cache.put("ethusdt", "5m", _ms(2024), _ms(2024, 6), _rows(3))

    conn = MagicMock()
    with patch(
        "backend.dataset.dataset_cache.fetch_db_rows",
        side_effect=MemoryError("OOM"),
    ):
        with pytest.raises(MemoryError):
            cache.get(conn, "btcusdt", "5m", _ms(2025), _ms(2025, 6))

    # Кэш должен быть очищен после MemoryError
    assert len(cache) == 0
    assert cache.memory_usage_bytes == 0


# ─────────────────────────────────────────────────────────────────────────────
# Синглтон из __init__.py
# ─────────────────────────────────────────────────────────────────────────────

def test_singleton_exported():
    from backend.dataset import dataset_cache
    assert isinstance(dataset_cache, DatasetCache)
    assert dataset_cache._max == 10
