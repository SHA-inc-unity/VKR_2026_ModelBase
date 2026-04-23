"""Менеджер фоновых задач для операций с датасетом.

Хранит потоки и их состояние в памяти процесса (module-level dict).
Данные сохраняются между перезагрузками Streamlit-страницы, пока
Python-процесс жив.

Публичный API::

    job_manager.submit(job_id, fn, *args, **kwargs)  — запустить в фоне
    job_manager.get(job_id)                          — получить состояние
    job_manager.update(job_id, **fields)             — обновить из потока
    job_manager.cleanup_old()                        — удалить старые задачи
"""
from __future__ import annotations

import threading
import time as _time
from typing import Any, Callable

_LOCK: threading.RLock = threading.RLock()
_JOBS: dict[str, dict] = {}


def submit(job_id: str, fn: Callable, *args: Any, **kwargs: Any) -> None:
    """Запускает ``fn(*args, **kwargs)`` в демон-потоке и сохраняет состояние."""
    started_at = _time.monotonic()
    with _LOCK:
        _JOBS[job_id] = {
            "status": "running",
            "progress": 0.0,
            "status_text": "Запуск...",
            "result": None,
            "error": None,
            "started_at": started_at,
        }

    def _run() -> None:
        try:
            result = fn(*args, **kwargs)
            with _LOCK:
                _JOBS[job_id]["status"] = "done"
                _JOBS[job_id]["result"] = result
                _JOBS[job_id]["status_text"] = "Готово"
        except Exception as exc:
            with _LOCK:
                _JOBS[job_id]["status"] = "error"
                _JOBS[job_id]["error"] = str(exc)
                _JOBS[job_id]["status_text"] = f"Ошибка: {exc}"

    t = threading.Thread(
        target=_run,
        daemon=True,
        name=f"ds-dl-{job_id[:8]}",
    )
    with _LOCK:
        _JOBS[job_id]["thread"] = t
    t.start()


def get(job_id: str) -> dict | None:
    """Возвращает копию состояния задачи или None если не найдена."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        return {k: v for k, v in job.items() if k != "thread"}


def update(job_id: str, **kwargs: Any) -> None:
    """Обновляет поля состояния задачи (вызывается из фонового потока)."""
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kwargs)


def cleanup_old(max_keep: int = 20) -> None:
    """Удаляет завершённые задачи, оставляя не более max_keep последних."""
    with _LOCK:
        done_ids = [jid for jid, j in _JOBS.items() if j.get("status") != "running"]
        for jid in done_ids[max_keep:]:
            _JOBS.pop(jid, None)


__all__ = ["submit", "get", "update", "cleanup_old"]
