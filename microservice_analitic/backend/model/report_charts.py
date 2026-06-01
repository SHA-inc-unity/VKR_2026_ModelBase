"""Графики отчёта: feature importance TOP-N, actual vs predicted, cumulative P&L.

Выделено из ``report.py`` без изменения логики. Публичные имена ре-экспортируются
из ``report`` для обратной совместимости импортов.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

_LOG = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MATPLOTLIB = True
except ImportError:
    _HAS_MATPLOTLIB = False

from backend.dataset.core import log

from .config import MODELS_DIR


# ---------------------------------------------------------------------------
# Feature importance TOP-N
# ---------------------------------------------------------------------------

def plot_feature_importance(
    model: object,
    feature_names: list[str],
    *,
    top_n: int = 20,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Строит горизонтальный bar-chart топ-N важных признаков и сохраняет PNG.

    Использует встроенный метод get_feature_importance() CatBoost
    (предсказание на основе разбиений в деревьях, не SHAP).
    """
    if not _HAS_MATPLOTLIB:
        raise ImportError("Установи matplotlib: pip install matplotlib")

    importances = model.get_feature_importance()  # type: ignore[attr-defined]
    fi = pd.Series(importances, index=feature_names).sort_values(ascending=False)
    top = fi.head(top_n)

    fig, ax = plt.subplots(figsize=(10, max(4, top_n // 3)))
    top[::-1].plot(kind="barh", ax=ax, color="#4C72B0", edgecolor="none")
    ax.set_xlabel("Важность признака (%)", fontsize=11)
    ax.set_title(f"TOP-{top_n} Feature Importance — {prefix}", fontsize=13)
    ax.tick_params(axis="y", labelsize=9)
    ax.grid(axis="x", alpha=0.3)
    plt.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_feature_importance.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log(f"[report] Feature importance → {path}")
    return path


# ---------------------------------------------------------------------------
# Actual vs Predicted
# ---------------------------------------------------------------------------

def plot_actual_vs_predicted(
    y_true: "np.ndarray | pd.Series",
    y_pred: np.ndarray,
    timestamps: "pd.Series | None" = None,
    *,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Строит временно́й график actual vs predicted и сохраняет PNG."""
    if not _HAS_MATPLOTLIB:
        raise ImportError("Установи matplotlib: pip install matplotlib")

    y_true_arr = np.asarray(y_true)
    x = timestamps.values if timestamps is not None else np.arange(len(y_true_arr))

    fig, ax = plt.subplots(figsize=(16, 4))
    ax.plot(x, y_true_arr, label="Actual",    alpha=0.75, linewidth=0.9, color="#2196F3")
    ax.plot(x, y_pred,     label="Predicted", alpha=0.75, linewidth=0.9, color="#F44336")
    ax.axhline(0.0, color="gray", linewidth=0.6, linestyle="--")
    ax.set_ylabel("target_return_1", fontsize=11)
    ax.set_title(f"Actual vs Predicted — {prefix}", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(alpha=0.25)
    plt.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_actual_vs_predicted.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log(f"[report] Actual vs Predicted → {path}")
    return path


# ---------------------------------------------------------------------------
# Cumulative P&L curve
# ---------------------------------------------------------------------------

def plot_cumulative_pnl(
    y_true: "np.ndarray | pd.Series",
    y_pred: np.ndarray,
    timestamps: "pd.Series | None" = None,
    *,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Строит график Cumulative P&L (накопленный доход стратегии long/short) и сохраняет PNG.

    Стратегия: sign(pred) * actual на каждом шаге.
    Добавляется линия Buy&Hold (накопленный actual) для сравнения.
    """
    if not _HAS_MATPLOTLIB:
        raise ImportError("Установи matplotlib: pip install matplotlib")

    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)
    x = timestamps.values if timestamps is not None else np.arange(len(y_true_arr))

    strategy_ret  = np.sign(y_pred_arr) * y_true_arr
    cum_strategy  = np.cumsum(strategy_ret)
    cum_bh        = np.cumsum(y_true_arr)

    fig, ax = plt.subplots(figsize=(16, 4))
    ax.plot(x, cum_strategy, label="Strategy (sign pred)", linewidth=1.2, color="#4CAF50")
    ax.plot(x, cum_bh,       label="Buy & Hold",           linewidth=1.0, color="#2196F3", alpha=0.7, linestyle="--")
    ax.axhline(0.0, color="gray", linewidth=0.6, linestyle=":")
    ax.set_ylabel("Cumulative return", fontsize=11)
    ax.set_title(f"Cumulative P&L — {prefix}", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(alpha=0.25)
    plt.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}_cumulative_pnl.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log(f"[report] Cumulative P&L → {path}")
    return path
