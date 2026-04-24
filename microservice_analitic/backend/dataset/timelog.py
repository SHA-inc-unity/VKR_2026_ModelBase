"""Тайм-лог загрузки датасета.

Пишет структурированные записи с таймингами в logs/dataset.log
(ротирующий файл 10 MB × 3 копии).

Использование в других модулях:
    from .timelog import now, tlog

    t0 = now()
    ...тяжёлая операция...
    tlog.info("fetch_funding_rates | DONE rows=%d elapsed=%.3fs", n, now() - t0)

Файл лога доступен напрямую по пути:
    <project_root>/logs/dataset.log
"""
from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Папка logs/ в корне проекта (рядом с backend/, frontend/, tests/) ────────
# timelog.py живёт в backend/dataset/ → поднимаемся на 2 уровня вверх
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass  # только что монтированный read-only слой — ничего страшного

_LOG_FILE = _LOG_DIR / "dataset.log"

# ── Форматтер ─────────────────────────────────────────────────────────────────
_formatter = logging.Formatter(
    "%(asctime)s.%(msecs)03d [%(levelname)-5s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Хэндлер: ротация при 10 MB, 3 резервных файла ────────────────────────────
try:
    _file_handler = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setFormatter(_formatter)
    _file_handler.setLevel(logging.DEBUG)
except OSError:
    _file_handler = None  # type: ignore[assignment]

# ── Логгер ────────────────────────────────────────────────────────────────────
tlog: logging.Logger = logging.getLogger("backend.dataset.timelog")
tlog.setLevel(logging.DEBUG)
tlog.propagate = False  # не дублировать в корневой логгер
if _file_handler is not None and not tlog.handlers:
    tlog.addHandler(_file_handler)


def now() -> float:
    """Монотонный счётчик для замера интервалов (секунды, float)."""
    return time.perf_counter()


from contextlib import contextmanager
from typing import Iterator

try:
    import os as _os
    import psutil as _psutil
    _PROC = _psutil.Process(_os.getpid())
except Exception:  # psutil не обязателен
    _PROC = None


def _rss_mb() -> float | None:
    """Текущий RSS процесса в МБ, либо None если psutil недоступен."""
    if _PROC is None:
        return None
    try:
        return _PROC.memory_info().rss / (1024 * 1024)
    except Exception:
        return None


@contextmanager
def perf_stage(name: str, **context) -> Iterator[dict]:
    """Контекст-менеджер для замера длительности стадии и структурированного лога.

    Пример:
        with perf_stage("download_missing.find_gaps", table=table_name):
            missing = builder.find_missing_timestamps_sql(...)

    Лог получит строку вида:
        download_missing.find_gaps | START table=...
        download_missing.find_gaps | DONE elapsed=0.045s rss=1234.5MB drss=+12.3MB table=...

    Если psutil установлен — добавляется RSS процесса на входе/выходе и дельта.
    Внутри блока можно мутировать yielded dict, чтобы добавить метрики
    (например rows=len(result)), которые попадут в финальную строку DONE.
    """
    ctx = dict(context)
    ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items()) if ctx else ""
    rss_start = _rss_mb()
    rss_str = f" rss={rss_start:.1f}MB" if rss_start is not None else ""
    tlog.info("%s | START%s %s", name, rss_str, ctx_str)
    t0 = time.perf_counter()
    cpu0 = time.process_time()
    extra: dict = {}
    try:
        yield extra
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        cpu_elapsed = time.process_time() - cpu0
        cpu_util = (cpu_elapsed / elapsed * 100.0) if elapsed > 0 else 0.0
        rss_end = _rss_mb()
        rss_str = (
            f" rss={rss_end:.1f}MB drss={rss_end - rss_start:+.1f}MB"
            if rss_end is not None and rss_start is not None
            else ""
        )
        extra_str = " ".join(f"{k}={v}" for k, v in {**ctx, **extra}.items())
        tlog.exception(
            "%s | FAILED elapsed=%.3fs cpu=%.3fs cpu_util=%.1f%%%s %s error=%s",
            name, elapsed, cpu_elapsed, cpu_util, rss_str, extra_str, type(exc).__name__,
        )
        raise
    else:
        elapsed = time.perf_counter() - t0
        cpu_elapsed = time.process_time() - cpu0
        cpu_util = (cpu_elapsed / elapsed * 100.0) if elapsed > 0 else 0.0
        rss_end = _rss_mb()
        rss_str = (
            f" rss={rss_end:.1f}MB drss={rss_end - rss_start:+.1f}MB"
            if rss_end is not None and rss_start is not None
            else ""
        )
        extra_str = " ".join(f"{k}={v}" for k, v in {**ctx, **extra}.items())
        tlog.info(
            "%s | DONE elapsed=%.3fs cpu=%.3fs cpu_util=%.1f%%%s %s",
            name, elapsed, cpu_elapsed, cpu_util, rss_str, extra_str,
        )
