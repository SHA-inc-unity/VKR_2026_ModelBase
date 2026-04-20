"""Общие утилиты пакета backend."""
from __future__ import annotations

import datetime

import numpy as np


def to_json_safe(obj: object) -> object:
    """Рекурсивно конвертирует numpy-типы в Python-примитивы для JSON-сериализации."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_json_safe(x) for x in obj]
    return obj


def now_utc() -> str:
    """Возвращает текущее UTC-время в формате ISO 8601 с суффиксом Z."""
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
