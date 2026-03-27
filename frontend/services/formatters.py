from __future__ import annotations

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
