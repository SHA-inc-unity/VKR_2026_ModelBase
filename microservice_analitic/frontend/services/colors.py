"""Центральная палитра цветов для ML-графиков model_page.py / compare_page.py.

Разделение: семантические имена → hex, чтобы смена темы или брендинга
требовала правки только здесь, а не в каждом chart-builder'е.

Импорт::

    from services.colors import C

    bar = go.Bar(... marker_color=C.feature_importance)
"""
from __future__ import annotations


class _Palette:
    """Immutable semantic colour palette for ML charts."""

    __slots__ = ()

    # --- Actual vs Predicted ---
    actual:    str = "#2196F3"   # blue
    predicted: str = "#F44336"   # red

    # --- Cumulative P&L ---
    strategy:     str = "#4CAF50"  # green  — sign(pred) · actual
    buy_and_hold: str = "#90A4AE"  # grey   — baseline

    # --- Feature Importance (bar chart) ---
    feature_importance: str = "#4C72B0"  # slate-blue

    # --- SHAP summary ---
    shap: str = "#9C27B0"  # purple

    # --- Live progress chart (Grid Search / Optuna) ---
    progress_trial:   str = "#2196F3"  # blue    — per-trial Sharpe dots
    progress_best:    str = "#4CAF50"  # green   — running-best line
    progress_rmse:    str = "#F44336"  # red     — RMSE line (secondary axis)

    # --- Learning curve ---
    lc_val_rmse:  str = "#2196F3"  # blue
    lc_best_iter: str = "#F44336"  # red  — vertical line at best iteration

    # --- Multi-session overlay palette (compare_page.py) ---
    overlay: tuple[str, ...] = (
        "#4CAF50",  # green
        "#2196F3",  # blue
        "#F44336",  # red
        "#9C27B0",  # purple
        "#FF9800",  # orange
        "#00BCD4",  # cyan
        "#E91E63",  # pink
        "#607D8B",  # blue-grey
    )

    # --- Neutral ---
    zero_line: str = "gray"


#: Singleton palette instance — use ``C.actual``, ``C.strategy``, etc.
C = _Palette()

__all__ = ["C"]
