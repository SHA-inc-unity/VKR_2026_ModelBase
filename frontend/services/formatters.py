from __future__ import annotations

import math
from typing import Optional


def fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "—"


def fmt_number(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return "—"


def fmt_delta(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+,.{digits}f}"
    except Exception:
        return "—"


def fmt_percent(value: Optional[float], digits: int = 2, scale_100: bool = False) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
        if scale_100:
            v *= 100
        return f"{v:.{digits}f}%"
    except Exception:
        return "—"


def fmt_confidence(value: Optional[float]) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
        if v <= 1:
            v *= 100
        return f"{v:.1f}%"
    except Exception:
        return "—"


def fmt_bool(value: Optional[bool]) -> str:
    if value is None:
        return "—"
    return "Yes" if bool(value) else "No"


def fmt_text(value: Optional[object]) -> str:
    if value in (None, ""):
        return "—"
    return str(value)


def fmt_duration_seconds(value: Optional[float]) -> str:
    if value is None:
        return "—"
    try:
        total_seconds = max(0, int(round(float(value))))
    except Exception:
        return "—"
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def fmt_cpu_percent(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        numeric = float(value)
    except Exception:
        return "—"
    if math.isnan(numeric):
        return "—"
    return f"{numeric:.{digits}f}%"
