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


@contextmanager
def perf_stage(name: str, **context) -> Iterator[dict]:
    """Контекст-менеджер для замера длительности стадии и структурированного лога.

    Пример:
        with perf_stage("download_missing.find_gaps", table=table_name):
            missing = builder.find_missing_timestamps_sql(...)

    Лог получит строку вида:
        download_missing.find_gaps | START table=...
        download_missing.find_gaps | DONE elapsed=0.045s table=... extra=...

    Внутри блока можно мутировать yielded dict, чтобы добавить метрики
    (например rows=len(result)), которые попадут в финальную строку DONE.
    """
    ctx = dict(context)
    ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items()) if ctx else ""
    tlog.info("%s | START %s", name, ctx_str)
    t0 = time.perf_counter()
    extra: dict = {}
    try:
        yield extra
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        extra_str = " ".join(f"{k}={v}" for k, v in {**ctx, **extra}.items())
        tlog.exception(
            "%s | FAILED elapsed=%.3fs %s error=%s",
            name, elapsed, extra_str, type(exc).__name__,
        )
        raise
    else:
        elapsed = time.perf_counter() - t0
        extra_str = " ".join(f"{k}={v}" for k, v in {**ctx, **extra}.items())
        tlog.info("%s | DONE elapsed=%.3fs %s", name, elapsed, extra_str)
