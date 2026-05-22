"""Многостраничный PDF-отчёт по обученной сессии (matplotlib PdfPages).

Страницы:
  1. Заголовок + ключевые метрики + best_params
  2. Learning curve (val_rmse vs iteration из overfit_diagnostics)
  3. Feature importance TOP-N (model.get_feature_importance)
  4. Actual vs Predicted (временной ряд по y_test / y_pred / ts_test)
  5. Cumulative P&L (sign(pred) · actual) vs Buy & Hold
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    _HAS_MATPLOTLIB = True
except ImportError:
    _HAS_MATPLOTLIB = False

from backend.dataset.core import log

from .config import MODELS_DIR


def _fmt_num(v: Any, digits: int = 4) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _page_summary(
    pdf: "PdfPages",
    *,
    prefix: str,
    metrics: dict,
    best_params: dict,
    target_col: str,
    n_features: int,
    n_test: int,
) -> None:
    """Страница 1: метрики + гиперпараметры в виде таблиц."""
    fig, ax = plt.subplots(figsize=(11.69, 8.27))  # A4 landscape inches
    ax.axis("off")

    ax.text(
        0.02, 0.97,
        f"Отчёт сессии: {prefix}",
        fontsize=18, fontweight="bold", transform=ax.transAxes,
    )
    ax.text(
        0.02, 0.93,
        f"target = {target_col}   ·   признаков = {n_features}   ·   test-баров = {n_test:,}",
        fontsize=10, color="#555", transform=ax.transAxes,
    )

    # Таблица ключевых метрик
    metric_order = ["sharpe", "RMSE", "MAE", "R2", "dir_acc_pct", "mae_pct", "profit_factor"]
    mrows = [[k, _fmt_num(metrics.get(k), 6 if k in ("RMSE", "MAE") else 4)]
             for k in metric_order if k in metrics]
    if mrows:
        tbl = ax.table(
            cellText=mrows,
            colLabels=["Метрика", "Значение"],
            cellLoc="left", colLoc="left",
            bbox=[0.02, 0.55, 0.45, 0.32],
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(10)

    # Таблица гиперпараметров
    prows = [[k, _fmt_num(v, 4) if isinstance(v, float) else str(v)]
             for k, v in best_params.items()]
    if prows:
        tbl2 = ax.table(
            cellText=prows,
            colLabels=["Параметр", "Значение"],
            cellLoc="left", colLoc="left",
            bbox=[0.53, 0.55, 0.45, 0.32],
        )
        tbl2.auto_set_font_size(False)
        tbl2.set_fontsize(10)

    # Заголовки под таблицами
    ax.text(0.02, 0.88, "Метрики на тесте", fontsize=12, fontweight="bold", transform=ax.transAxes)
    ax.text(0.53, 0.88, "Гиперпараметры", fontsize=12, fontweight="bold", transform=ax.transAxes)

    pdf.savefig(fig)
    plt.close(fig)


def _page_learning_curve(pdf: "PdfPages", overfit_diag: "dict | None") -> None:
    """Страница 2: кривая валидационной RMSE по итерациям boosting."""
    fig, ax = plt.subplots(figsize=(11.69, 6.5))
    lc = (overfit_diag or {}).get("learning_curve") or {}
    iters = lc.get("iterations") or []
    rmse  = lc.get("val_rmse") or []
    best  = lc.get("best_iteration")
    if iters and rmse:
        ax.plot(iters, rmse, color="#2196F3", linewidth=1.2, label="val RMSE")
        if best is not None and 0 <= best < len(iters):
            ax.axvline(best, color="#F44336", linestyle="--", linewidth=1.0, label=f"best iter = {best}")
            ax.scatter([best], [rmse[best]], color="#F44336", zorder=5)
        ax.set_xlabel("boosting iteration")
        ax.set_ylabel("validation RMSE")
        ax.grid(alpha=0.25)
        ax.legend()
    else:
        ax.text(0.5, 0.5, "Нет данных learning_curve", ha="center", va="center", fontsize=12, color="#888")
        ax.axis("off")
    ax.set_title("Learning curve (validation RMSE)", fontsize=13, fontweight="bold")
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def _page_feature_importance(
    pdf: "PdfPages",
    *,
    model: Any,
    feature_cols: list[str],
    top_n: int = 20,
) -> None:
    """Страница 3: горизонтальный bar-chart топ-N feature importance."""
    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    try:
        fi_raw = np.asarray(model.get_feature_importance(), dtype=float)
        fi = pd.Series(fi_raw, index=feature_cols).sort_values(ascending=False).head(top_n)
        fi = fi.sort_values(ascending=True)
        ax.barh(fi.index.tolist(), fi.values, color="#4C72B0")
        ax.set_xlabel("Важность (%)")
        ax.set_title(f"Feature Importance — TOP {top_n}", fontsize=13, fontweight="bold")
        ax.grid(axis="x", alpha=0.25)
    except Exception as exc:
        ax.text(0.5, 0.5, f"feature_importance недоступно: {exc}",
                ha="center", va="center", fontsize=11, color="#888")
        ax.axis("off")
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def _page_actual_vs_pred(
    pdf: "PdfPages",
    *,
    y_test: np.ndarray,
    y_pred: np.ndarray,
    ts_test: "pd.Series | np.ndarray | None",
    target_col: str,
) -> None:
    """Страница 4: actual и predicted во времени."""
    fig, ax = plt.subplots(figsize=(11.69, 5.5))
    x = ts_test if ts_test is not None else np.arange(len(y_test))
    ax.plot(x, y_test, color="#2196F3", linewidth=0.9, alpha=0.85, label="Actual")
    ax.plot(x, y_pred, color="#F44336", linewidth=0.9, alpha=0.85, label="Predicted")
    ax.axhline(0, color="gray", linewidth=0.5, linestyle=":")
    ax.set_xlabel("Время")
    ax.set_ylabel(target_col)
    ax.set_title("Actual vs Predicted — тестовая выборка", fontsize=13, fontweight="bold")
    ax.legend(loc="upper right")
    ax.grid(alpha=0.25)
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def _page_cumulative_pnl(
    pdf: "PdfPages",
    *,
    y_test: np.ndarray,
    y_pred: np.ndarray,
    ts_test: "pd.Series | np.ndarray | None",
) -> None:
    """Страница 5: кумулятивный доход стратегии sign(pred) vs Buy & Hold."""
    fig, ax = plt.subplots(figsize=(11.69, 5.5))
    x = ts_test if ts_test is not None else np.arange(len(y_test))
    strategy = np.sign(y_pred) * y_test
    cum_strategy = np.cumsum(strategy)
    cum_bh       = np.cumsum(y_test)
    ax.plot(x, cum_strategy, color="#4CAF50", linewidth=1.2, label="Strategy (sign pred)")
    ax.plot(x, cum_bh, color="#2196F3", linewidth=1.0, linestyle="--", alpha=0.75, label="Buy & Hold")
    ax.axhline(0, color="gray", linewidth=0.5, linestyle=":")
    ax.set_xlabel("Время")
    ax.set_ylabel("Cumulative return")
    ax.set_title("Cumulative P&L", fontsize=13, fontweight="bold")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)
    plt.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def generate_session_pdf(
    *,
    prefix: str,
    model: Any,
    metrics: dict,
    best_params: dict,
    feature_cols: list[str],
    y_test: "np.ndarray | pd.Series",
    y_pred: "np.ndarray | pd.Series",
    ts_test: "pd.Series | np.ndarray | None",
    target_col: str = "target_return_1",
    overfit_diagnostics: "dict | None" = None,
    top_n_fi: int = 20,
    output_dir: Path = MODELS_DIR,
    output_path: "Path | None" = None,
) -> Path:
    """Генерирует многостраничный PDF-отчёт для данной сессии.

    Если output_path не задан, сохраняет в MODELS_DIR/{prefix}_report.pdf.
    Возвращает путь к записанному файлу.
    """
    if not _HAS_MATPLOTLIB:
        raise ImportError("matplotlib не установлен. Выполните: pip install matplotlib")

    y_test_arr = np.asarray(y_test, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)
    if output_path is None:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{prefix}_report.pdf"

    with PdfPages(output_path) as pdf:
        _page_summary(
            pdf,
            prefix=prefix, metrics=metrics, best_params=best_params,
            target_col=target_col, n_features=len(feature_cols),
            n_test=len(y_test_arr),
        )
        _page_learning_curve(pdf, overfit_diagnostics)
        _page_feature_importance(
            pdf, model=model, feature_cols=feature_cols, top_n=top_n_fi,
        )
        _page_actual_vs_pred(
            pdf, y_test=y_test_arr, y_pred=y_pred_arr,
            ts_test=ts_test, target_col=target_col,
        )
        _page_cumulative_pnl(
            pdf, y_test=y_test_arr, y_pred=y_pred_arr, ts_test=ts_test,
        )

        meta = pdf.infodict()
        meta["Title"]   = f"ModelLine session report — {prefix}"
        meta["Author"]  = "ModelLine"
        meta["Subject"] = f"CatBoost {target_col}"

    log(f"[pdf] отчёт сохранён → {output_path}")
    return output_path


def generate_session_pdf_bytes(**kwargs: Any) -> bytes:
    """То же самое, но возвращает PDF в виде bytes (для st.download_button)."""
    if not _HAS_MATPLOTLIB:
        raise ImportError("matplotlib не установлен. Выполните: pip install matplotlib")

    y_test_arr = np.asarray(kwargs["y_test"], dtype=float)
    y_pred_arr = np.asarray(kwargs["y_pred"], dtype=float)
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        _page_summary(
            pdf,
            prefix=kwargs["prefix"],
            metrics=kwargs.get("metrics") or {},
            best_params=kwargs.get("best_params") or {},
            target_col=kwargs.get("target_col") or "target_return_1",
            n_features=len(kwargs.get("feature_cols") or []),
            n_test=len(y_test_arr),
        )
        _page_learning_curve(pdf, kwargs.get("overfit_diagnostics"))
        _page_feature_importance(
            pdf,
            model=kwargs["model"],
            feature_cols=kwargs.get("feature_cols") or [],
            top_n=int(kwargs.get("top_n_fi", 20)),
        )
        _page_actual_vs_pred(
            pdf,
            y_test=y_test_arr, y_pred=y_pred_arr,
            ts_test=kwargs.get("ts_test"),
            target_col=kwargs.get("target_col") or "target_return_1",
        )
        _page_cumulative_pnl(
            pdf,
            y_test=y_test_arr, y_pred=y_pred_arr,
            ts_test=kwargs.get("ts_test"),
        )
    return buf.getvalue()
