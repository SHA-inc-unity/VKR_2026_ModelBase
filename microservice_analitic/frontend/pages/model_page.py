"""Страница обучения CatBoost-модели прогнозирования target_return_1."""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import psycopg2
import streamlit as st

# Пути: корень воркспейса и frontend-пакет
_HERE = Path(__file__).resolve()
_WORKSPACE_ROOT = _HERE.parents[2]
_FRONTEND_ROOT = _HERE.parents[1]
for _p in (_WORKSPACE_ROOT, _FRONTEND_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import datetime as _dt

from backend import dataset as builder
from pages.download_page import download_missing, make_request
from backend.model import (
    cache_stats,
    clear_cache,
    compute_overfitting_diagnostics,
    generate_session_pdf_bytes,
    grid_search_cv,
    list_target_candidates,
    load_cached_dataset,
    load_training_data,
    optuna_search_cv,
    save_cached_dataset,
    save_model,
    train_final_model,
    walk_forward_split,
)
from backend.model.config import (
    DEFAULT_PARAM_VALUES,
    MODELS_DIR,
    PARAM_GRID,
    TARGET_COLUMN,
    _PARAM_TYPES,
    expand_param_grid,
)
from backend.model.metrics import compute_direction_metrics, compute_trading_metrics
from backend.model.report import (
    compute_shap_values,
    delete_registry_version,
    load_grid_best_params,
    load_grid_session_result,
    load_optuna_best_params,
    load_optuna_session_result,
    load_registry,
    load_session_result,
    load_shap_summary,
    save_grid_best_params,
    save_grid_results,
    save_predictions_json,
    save_results_json,
    save_session_result,
    save_shap_summary,
)
from services.colors import C as _C
from services.i18n import t
from services.ui_components import render_back_button, render_db_status, render_lang_toggle
from services.trainer import (
    clear_status,
    is_thread_alive,
    read_status,
    start_grid_search,
    start_optuna_search,
    start_training_pipeline,
)
from services.db_auth import (
    load_ui_prefs,
    save_ui_prefs,
    clear_grid_params_config,
    clear_local_config,
    load_db_config,
    load_grid_params_config,
    load_local_config,
    save_grid_params_config,
    save_local_config,
)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

_SECONDS_PER_YEAR: float = 365.25 * 24 * 3600

_GRID_DISPLAY_COLS = [
    "rank", "combo", "iterations", "depth", "learning_rate", "l2_leaf_reg",
    "bagging_temperature", "border_count",
    "mean_rmse_cv", "std_rmse_cv",
    "sharpe", "dir_acc_pct", "mae_pct", "profit_factor",
    "accuracy", "elapsed_s",
]

# Конфиг столбцов для таблицы «параметр → значения»
_VALUES_COL_CFG = {
    "параметр": st.column_config.TextColumn(
        "Параметр", width="medium", disabled=True
    ),
    "значения": st.column_config.TextColumn(
        "Значения (через запятую)", width="large"
    ),
}

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def connect_db(config: dict) -> psycopg2.extensions.connection:
    """Подключается к PostgreSQL."""
    params = {"host": config["host"], "port": config["port"], "dbname": config["database"]}
    if config.get("user"):
        params["user"] = config["user"]
    if config.get("password"):
        params["password"] = config["password"]
    return psycopg2.connect(**params)


def probe_db_connection(config: dict) -> dict:
    """Проверяет подключение к БД."""
    try:
        conn = connect_db(config)
        conn.close()
        return {"connected": True, "message": ""}
    except Exception as exc:
        hint = ""
        if not config.get("user") or not config.get("password"):
            hint = " PGUSER/PGPASSWORD не заданы — требуется local trust auth."
        return {"connected": False, "message": f"{exc}{hint}"}


def _show_error(
    context: str,
    exc_or_msg: "Exception | str",
    *,
    tb: "str | None" = None,
) -> None:
    """Единая точка отображения ошибок в UI.

    Показывает красную плашку `{context}: {сообщение}` и скрытый traceback
    в st.expander. Если передан Exception — traceback берётся из sys.exc_info(),
    иначе используется параметр `tb` (для ошибок из фоновых потоков).
    """
    if isinstance(exc_or_msg, BaseException):
        msg = f"{type(exc_or_msg).__name__}: {exc_or_msg}"
        if tb is None:
            captured = traceback.format_exc()
            if captured and captured.strip() and captured.strip() != "NoneType: None":
                tb = captured
    else:
        msg = str(exc_or_msg)

    st.error(f"❌ {context}: {msg}")
    if tb and tb.strip():
        with st.expander("Детали (traceback)"):
            st.code(tb, language="python")


def _load_and_cache_data(
    config: dict, sym: str, tf: str,
    date_from: str | None = None,
    date_to: str | None = None,
    target_col: str | None = None,
    *,
    use_disk_cache: bool | None = None,
) -> dict:
    """Загружает данные из persistent parquet-кеша или PostgreSQL; держит в session_state.

    Порядок:
      1) session_state (in-memory) — если ключ совпадает;
      2) disk cache (MODELS_DIR/cache/{hash}.parquet) — если включён;
      3) SELECT из PostgreSQL + сохранение в disk cache.
    """
    tbl = builder.make_table_name(sym, tf)
    target = target_col or TARGET_COLUMN
    data_key = (
        sym, tf,
        config["host"], str(config["port"]), config["database"],
        str(date_from), str(date_to),
        target,
    )
    if (
        st.session_state.get("loaded_data_key") == data_key
        and st.session_state.get("loaded_data") is not None
    ):
        return st.session_state.loaded_data  # type: ignore[return-value]

    if use_disk_cache is None:
        use_disk_cache = bool(st.session_state.get("m_use_disk_cache", True))

    X = y = feature_cols = timestamps = None
    if use_disk_cache:
        hit = load_cached_dataset(
            tbl, date_from=date_from, date_to=date_to, target_col=target,
        )
        if hit is not None:
            X, y, feature_cols, timestamps = hit

    if X is None:
        conn = connect_db(config)
        try:
            X, y, feature_cols, timestamps = load_training_data(
                conn, tbl, date_from=date_from, date_to=date_to,
                target_col=target,
            )
        finally:
            conn.close()
        if use_disk_cache:
            try:
                save_cached_dataset(
                    X, y, feature_cols, timestamps,
                    table_name=tbl, date_from=date_from, date_to=date_to,
                    target_col=target,
                )
            except Exception as exc:
                st.warning(f"Не удалось сохранить disk-кеш: {exc}")

    train_size, test_size = walk_forward_split(len(X))
    data = {
        "X": X,
        "y": y,
        "feature_cols": feature_cols,
        "timestamps": timestamps,
        "train_size": train_size,
        "test_size": test_size,
        "table_name": tbl,
        "date_from": date_from,
        "date_to": date_to,
        "target_col": target,
    }
    st.session_state.loaded_data = data
    st.session_state.loaded_data_key = data_key
    return data


_AUTO_DOWNLOAD_DAYS = 365 * 3  # по умолчанию скачиваем последние 3 года


def _auto_download(
    config: dict,
    sym: str,
    tf: str,
    date_from: str | None,
    date_to: str | None,
    progress_bar,
    status_placeholder,
) -> None:
    """Скачивает данные с Bybit, если таблица отсутствует или пустая."""
    end_date   = _dt.date.today()
    start_date = (
        _dt.date.fromisoformat(date_from.split(" ")[0]) if date_from
        else end_date - _dt.timedelta(days=_AUTO_DOWNLOAD_DAYS)
    )
    if date_to:
        end_date = _dt.date.fromisoformat(date_to.split(" ")[0])

    req = make_request(sym.upper(), tf, start_date, end_date)
    download_missing(config, req, progress_bar=progress_bar, status_placeholder=status_placeholder)


def _load_and_cache_data_auto(
    config: dict, sym: str, tf: str,
    date_from: str | None = None,
    date_to: str | None = None,
    target_col: str | None = None,
    *,
    use_disk_cache: bool | None = None,
    status_container=None,
) -> dict:
    """Как _load_and_cache_data, но при отсутствии таблицы автоматически скачивает данные."""
    import psycopg2.errors as _pge

    try:
        return _load_and_cache_data(
            config, sym, tf, date_from, date_to, target_col,
            use_disk_cache=use_disk_cache,
        )
    except Exception as exc:
        # Проверяем: таблица не существует
        cause = exc.__cause__ or exc
        is_missing = isinstance(cause, _pge.UndefinedTable) or (
            "UndefinedTable" in type(cause).__name__
            or "does not exist" in str(exc).lower()
        )
        if not is_missing:
            raise

    # Таблицы нет — скачиваем автоматически
    ctx = status_container or st
    ctx.info(f"Таблица `{builder.make_table_name(sym.upper(), tf)}` не найдена. Скачиваю данные с Bybit...")
    _pb  = ctx.progress(0)
    _sph = ctx.empty()
    try:
        _auto_download(config, sym, tf, date_from, date_to, _pb, _sph)
    except Exception as dl_exc:
        _pb.empty()
        _sph.empty()
        raise RuntimeError(f"Не удалось скачать данные: {dl_exc}") from dl_exc

    _pb.empty()
    _sph.empty()
    ctx.success("Данные загружены. Читаю датасет...")

    # Сбрасываем кеш target-кандидатов — таблица только что создана
    st.session_state.pop("_target_candidates_cache", None)

    return _load_and_cache_data(
        config, sym, tf, date_from, date_to, target_col,
        use_disk_cache=use_disk_cache,
    )


def _fetch_target_candidates(config: dict, table_name: str) -> list[str]:
    """Берёт список target_*-колонок из таблицы, с кешем в session_state.

    Кеш-ключ включает таблицу и координаты БД, чтобы переключение symbol/timeframe
    приводило к свежему запросу.
    """
    cache_key = (
        table_name,
        config["host"], str(config["port"]), config["database"],
    )
    cached = st.session_state.get("_target_candidates_cache")
    if cached and cached.get("key") == cache_key:
        return list(cached["values"])

    try:
        conn = connect_db(config)
    except Exception:
        return []
    try:
        values = list_target_candidates(conn, table_name)
    except Exception:
        values = []
    finally:
        conn.close()

    st.session_state["_target_candidates_cache"] = {"key": cache_key, "values": values}
    return values


def _default_values_df() -> pd.DataFrame:
    """Возвращает DataFrame по умолчанию для таблицы параметр→значения."""
    return pd.DataFrame([
        {"параметр": k, "значения": ", ".join(str(v) for v in vs)}
        for k, vs in DEFAULT_PARAM_VALUES.items()
    ])


def _parse_param_values(df: pd.DataFrame) -> dict[str, list]:
    """Парсит таблицу параметр→значения в dict для expand_param_grid.

    Каждая строка: параметр | '500, 1000, 1500'  →  {"iterations": [500, 1000, 1500]}
    """
    result: dict[str, list] = {}
    for _, row in df.iterrows():
        param = str(row["параметр"]).strip()
        cast = _PARAM_TYPES.get(param, float)
        values = [
            cast(v.strip())
            for v in str(row["значения"]).split(",")
            if v.strip()
        ]
        if values:
            result[param] = values
    return result


def _effective_param_values(param_values: dict[str, list], *, use_gpu: bool) -> dict[str, list]:
    """Возвращает значения параметров с учётом ограничений выбранного устройства."""
    if use_gpu:
        return {k: list(vs) for k, vs in param_values.items()}
    return {k: list(vs) for k, vs in param_values.items() if k != "border_count"}


def _count_effective_combos(param_values: dict[str, list], *, use_gpu: bool) -> int:
    """Считает число комбинаций после применения device-specific ограничений."""
    effective_values = _effective_param_values(param_values, use_gpu=use_gpu)
    total = 1
    for values in effective_values.values():
        total *= max(len(values), 1)
    return total


def _effective_param_grid(param_grid: list[dict], *, use_gpu: bool) -> list[dict]:
    """Нормализует список комбинаций под выбранное устройство и убирает дубликаты."""
    if use_gpu:
        return [dict(params) for params in param_grid]

    effective: list[dict] = []
    seen: set[tuple[tuple[str, object], ...]] = set()
    for params in param_grid:
        normalized = dict(params)
        normalized.pop("border_count", None)
        key = tuple(sorted(normalized.items()))
        if key in seen:
            continue
        seen.add(key)
        effective.append(normalized)
    return effective


def _render_grid_results_table(grid_df: pd.DataFrame, best_params: dict, top_n: int = 10) -> None:
    """Отрисовывает таблицу TOP-N результатов Grid Search (сортировка по Sharpe ↓).

    Столбцы: rank, combo, params, mean_rmse_cv, std_rmse_cv, sharpe, dir_acc_pct,
             mae_pct, profit_factor, accuracy, elapsed_s.
    """
    top_df = grid_df.head(top_n).copy()
    top_df.insert(0, "rank", range(1, len(top_df) + 1))
    available = [c for c in _GRID_DISPLAY_COLS if c in top_df.columns]

    fmt: dict[str, str] = {}
    for col, fmt_str in [
        ("mean_rmse_cv",  "{:.6f}"),
        ("std_rmse_cv",   "{:.6f}"),
        ("sharpe",        "{:.4f}"),
        ("dir_acc_pct",   "{:.2f}"),
        ("mae_pct",       "{:.4f}"),
        ("profit_factor", "{:.4f}"),
        ("accuracy",      "{:.4f}"),
        ("elapsed_s",     "{:.1f}"),
        ("learning_rate", "{:.4f}"),
    ]:
        if col in available:
            fmt[col] = fmt_str

    st.dataframe(
        top_df[available].style.format(fmt),
        width="stretch",
        hide_index=True,
    )

    # Лучшие параметры (первая строка = лучший по Sharpe)
    st.markdown("**★ Лучшая комбинация (по Sharpe):**")
    _pc = st.columns(len(best_params))
    for col, (k, v) in zip(_pc, best_params.items()):
        col.metric(k, str(v))


def _render_confusion_metrics(row: "pd.Series", label: str = "") -> None:
    """Рисует 5 метрик TP/TN/FP/FN/Accuracy из строки DataFrame."""
    if label:
        st.markdown(f"**{label}**")
    _cm = st.columns(5)
    _cm[0].metric("TP",       int(row.get("TP", 0)))
    _cm[1].metric("TN",       int(row.get("TN", 0)))
    _cm[2].metric("FP",       int(row.get("FP", 0)))
    _cm[3].metric("FN",       int(row.get("FN", 0)))
    _cm[4].metric("Accuracy", f"{float(row.get('accuracy', 0.0)):.4f}")


def build_feature_importance_chart(
    importances: list[float],
    feature_names: list[str],
    top_n: int = 20,
) -> go.Figure:
    """Горизонтальный bar-chart топ-N признаков (Plotly)."""
    fi = (
        pd.Series(importances, index=feature_names)
        .sort_values(ascending=False)
        .head(top_n)
        .sort_values(ascending=True)
    )
    fig = go.Figure(
        go.Bar(x=fi.values, y=fi.index.tolist(), orientation="h", marker_color=_C.feature_importance)
    )
    fig.update_layout(
        title=f"Feature Importance — TOP {top_n}",
        xaxis_title="Важность (%)",
        yaxis={"tickfont": {"size": 11}},
        height=max(350, top_n * 22),
        margin={"l": 200, "r": 20, "t": 40, "b": 40},
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def build_shap_summary_chart(
    mean_abs_shap: pd.Series,
    top_n: int = 20,
) -> go.Figure:
    """Горизонтальный bar-chart топ-N признаков по mean(|SHAP|) (Plotly, фиолетовый)."""
    fi = (
        mean_abs_shap.sort_values(ascending=False)
        .head(top_n)
        .sort_values(ascending=True)
    )
    fig = go.Figure(
        go.Bar(x=fi.values, y=fi.index.tolist(), orientation="h", marker_color=_C.shap)
    )
    fig.update_layout(
        title=f"SHAP Summary — TOP {top_n} (mean |SHAP|)",
        xaxis_title="Средний |SHAP|",
        yaxis={"tickfont": {"size": 11}},
        height=max(350, top_n * 22),
        margin={"l": 200, "r": 20, "t": 40, "b": 40},
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def build_live_progress_chart(
    history: list[dict],
    *,
    title: str = "Прогресс поиска",
    x_label: str = "combo #",
) -> go.Figure:
    """Live-график: Sharpe per комбинация + running-best + RMSE по вторичной оси.

    history: список словарей с ключами idx, sharpe, mean_rmse_cv, best_sharpe.
    """
    if not history:
        fig = go.Figure()
        fig.update_layout(title=f"{title} — ожидание первого результата…", height=320)
        return fig

    xs  = [int(h.get("idx", i + 1)) for i, h in enumerate(history)]
    shs = [float(h.get("sharpe", 0.0)) for h in history]
    bss = [float(h.get("best_sharpe", 0.0)) for h in history]
    rms = [float(h.get("mean_rmse_cv", 0.0)) for h in history]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=shs, mode="markers", name="Sharpe (trial)",
        marker={"color": _C.progress_trial, "size": 7}, opacity=0.75,
        yaxis="y1",
    ))
    fig.add_trace(go.Scatter(
        x=xs, y=bss, mode="lines", name="Running best Sharpe",
        line={"color": _C.progress_best, "width": 2.2},
        yaxis="y1",
    ))
    fig.add_trace(go.Scatter(
        x=xs, y=rms, mode="lines", name="mean_rmse_cv",
        line={"color": _C.progress_rmse, "width": 1.2, "dash": "dot"},
        opacity=0.6,
        yaxis="y2",
    ))
    fig.update_layout(
        title=title,
        xaxis_title=x_label,
        yaxis={"title": "Sharpe", "side": "left"},
        yaxis2={"title": "RMSE", "side": "right", "overlaying": "y", "showgrid": False},
        height=340,
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.10},
        margin={"t": 60, "b": 40},
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def build_actual_vs_predicted_chart(
    y_true: pd.Series,
    y_pred: np.ndarray,
    timestamps: pd.Series,
    *,
    target_label: str = TARGET_COLUMN,
) -> go.Figure:
    """Временно́й график actual vs predicted (Plotly)."""
    y_pred_arr = np.asarray(y_pred)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=timestamps, y=y_true.values,
        mode="lines", name="Actual",
        line={"color": _C.actual, "width": 1.2}, opacity=0.85,
    ))
    fig.add_trace(go.Scatter(
        x=timestamps, y=y_pred_arr,
        mode="lines", name="Predicted",
        line={"color": _C.predicted, "width": 1.2}, opacity=0.85,
    ))
    fig.add_hline(y=0, line_color=_C.zero_line, line_dash="dot", line_width=0.8)
    fig.update_layout(
        title="Actual vs Predicted — тестовая выборка",
        xaxis_title="Время", yaxis_title=target_label,
        height=340, hovermode="x unified",
        legend={"orientation": "h", "y": 1.04},
        margin={"t": 50, "b": 40},
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def build_cumulative_pnl_chart(
    y_true: pd.Series,
    y_pred: np.ndarray,
    timestamps: pd.Series,
) -> go.Figure:
    """Cumulative P&L: стратегия long/short vs Buy&Hold (Plotly)."""
    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)
    cum_strategy = np.cumsum(np.sign(y_pred_arr) * y_true_arr)
    cum_bh       = np.cumsum(y_true_arr)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=timestamps, y=cum_strategy,
        mode="lines", name="Strategy (sign pred)",
        line={"color": _C.strategy, "width": 1.4},
    ))
    fig.add_trace(go.Scatter(
        x=timestamps, y=cum_bh,
        mode="lines", name="Buy & Hold",
        line={"color": _C.buy_and_hold, "width": 1.1, "dash": "dash"}, opacity=0.8,
    ))
    fig.add_hline(y=0, line_color=_C.zero_line, line_dash="dot", line_width=0.7)
    fig.update_layout(
        title="Cumulative P&L — тестовая выборка",
        xaxis_title="Время", yaxis_title="Накопленный доход",
        height=340, hovermode="x unified",
        legend={"orientation": "h", "y": 1.04},
        margin={"t": 50, "b": 40},
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def build_learning_curve_chart(
    iterations: list[int],
    val_rmse: list[float],
    train_rmse_at_best: float,
    best_iteration: int,
) -> go.Figure:
    """Learning curve: val RMSE по итерациям + train RMSE как горизонтальная линия."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=iterations, y=val_rmse,
        mode="lines", name="Val RMSE",
        line={"color": _C.lc_val_rmse, "width": 1.5},
    ))
    fig.add_hline(
        y=train_rmse_at_best,
        line_dash="dash", line_color=_C.strategy, line_width=1.2,
        annotation_text=f"Train RMSE = {train_rmse_at_best:.6f}",
        annotation_position="bottom right",
    )
    if best_iteration > 0:
        fig.add_vline(
            x=best_iteration,
            line_dash="dot", line_color=_C.lc_best_iter, line_width=1.0,
            annotation_text=f"Best iter = {best_iteration}",
            annotation_position="top left",
        )
    fig.update_layout(
        title="Learning Curve — Val RMSE по итерациям",
        xaxis_title="Итерация", yaxis_title="RMSE",
        height=320, hovermode="x unified",
        legend={"orientation": "h", "y": 1.04},
        margin={"t": 50, "b": 40},
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ModelLine — " + t("model.title"), layout="wide", initial_sidebar_state="collapsed"
)

_hcols = st.columns([8, 1])
with _hcols[1]:
    render_lang_toggle(key="model_lang")

render_back_button()

st.title(t("model.title"))
st.caption(t("model.caption"))

# ---------------------------------------------------------------------------
# Session state + восстановление UI-предпочтений из store (Redis / SQLite)
# ---------------------------------------------------------------------------

for _k, _v in {
    "loaded_data":        None,
    "loaded_data_key":    None,
    "grid_result":        None,
    "optuna_result":      None,
    "model_result":       None,
    "model_symbol":       None,
    "model_timeframe":    None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# Загружаем сохранённые предпочтения один раз за сессию
if "_ui_prefs_loaded" not in st.session_state:
    _saved_prefs = load_ui_prefs()
    _TIMEFRAME_KEYS = list(builder.TIMEFRAMES.keys())
    for _widget_key, _pref_key, _default in [
        ("m_symbol",          "symbol",            "BTCUSDT"),
        ("m_use_disk_cache",  "use_disk_cache",     True),
        ("m_cv_mode",         "cv_mode",           "expanding"),
        ("m_max_train_size",  "max_train_size",     0),
        ("m_use_gpu",         "use_gpu",            False),
        ("m_n_trials",        "n_trials",           50),
        ("use_mlflow",        "use_mlflow",         False),
        ("mlflow_uri",        "mlflow_uri",        "http://localhost:5000"),
        ("mlflow_experiment", "mlflow_experiment", "ModelLine"),
    ]:
        if _widget_key not in st.session_state:
            st.session_state[_widget_key] = _saved_prefs.get(_pref_key, _default)
    # timeframe
    if "m_tf" not in st.session_state:
        _saved_tf = _saved_prefs.get("timeframe", "60m")
        if _saved_tf in _TIMEFRAME_KEYS:
            st.session_state["m_tf"] = _saved_tf
    # target_col
    if "m_target" not in st.session_state and _saved_prefs.get("target_col"):
        st.session_state["m_target"] = _saved_prefs["target_col"]
    # dates: restore ISO strings so date_input can pick them up
    if "m_date_from_iso" not in st.session_state:
        st.session_state["m_date_from_iso"] = _saved_prefs.get("date_from")
    if "m_date_to_iso" not in st.session_state:
        _dto = _saved_prefs.get("date_to")
        # strip the time part we appended when saving (" 23:59:59+00")
        if isinstance(_dto, str) and " " in _dto:
            _dto = _dto.split(" ")[0]
        st.session_state["m_date_to_iso"] = _dto
    st.session_state["_ui_prefs_loaded"] = True


def _date_to_iso(v: object) -> "str | None":
    """Конвертирует date-виджет значение → ISO строку (или None)."""
    if isinstance(v, _dt.date):
        return v.isoformat()
    return None


def _save_all_prefs() -> None:
    """Сохраняет текущие значения всех UI-виджетов в store (Redis / SQLite)."""
    # date_input хранит date-объект прямо в session_state по своему key
    _df = _date_to_iso(st.session_state.get("m_date_from"))
    _dt_raw = st.session_state.get("m_date_to")
    _dto = (_date_to_iso(_dt_raw) + " 23:59:59+00") if _dt_raw else None
    save_ui_prefs({
        "symbol":            st.session_state.get("m_symbol", "BTCUSDT"),
        "timeframe":         st.session_state.get("m_tf", "60m"),
        "date_from":         _df,
        "date_to":           _dto,
        "target_col":        st.session_state.get("m_target", TARGET_COLUMN),
        "cv_mode":           st.session_state.get("m_cv_mode", "expanding"),
        "max_train_size":    int(st.session_state.get("m_max_train_size", 0)),
        "use_gpu":           bool(st.session_state.get("m_use_gpu", False)),
        "n_trials":          int(st.session_state.get("m_n_trials", 50)),
        "use_disk_cache":    bool(st.session_state.get("m_use_disk_cache", True)),
        "use_mlflow":        bool(st.session_state.get("use_mlflow", False)),
        "mlflow_uri":        st.session_state.get("mlflow_uri", "http://localhost:5000"),
        "mlflow_experiment": st.session_state.get("mlflow_experiment", "ModelLine"),
    })

# ---------------------------------------------------------------------------
# DB подключение (roadmap #11 — свёрнут если подключено)
# ---------------------------------------------------------------------------

restored_config = load_db_config(load_local_config())

# Быстрая проверка до рендера expander чтобы знать expanded=
_quick_status = probe_db_connection(load_db_config(load_local_config()))

with st.expander(t("model.conn_expander"), expanded=not _quick_status["connected"]):
    _ov = st.columns(5)
    ov_host     = _ov[0].text_input(t("common.host"),     value=restored_config["host"],      key="m_host")
    ov_port     = _ov[1].text_input(t("common.port"),     value=str(restored_config["port"]), key="m_port")
    ov_database = _ov[2].text_input(t("common.database"), value=restored_config["database"],  key="m_db")
    ov_user     = _ov[3].text_input(t("common.user"),     value=restored_config["user"],      key="m_user")
    ov_password = _ov[4].text_input(
        t("common.password"), value=restored_config["password"], type="password", key="m_pass"
    )
    _sc = st.columns(2)
    if _sc[0].button(t("model.save_conn"), key="m_save"):
        save_local_config(load_db_config(
            {"host": ov_host, "port": ov_port, "database": ov_database,
             "user": ov_user, "password": ov_password}
        ))
        st.toast(t("model.conn_saved"), icon="✅")   # roadmap #14
    if _sc[1].button(t("model.clear_conn"), key="m_clear"):
        clear_local_config()
        st.rerun()

db_config = load_db_config(
    {"host": ov_host, "port": ov_port, "database": ov_database,
     "user": ov_user, "password": ov_password}
)
db_status = probe_db_connection(db_config)

# Shared DB status block (roadmap #4)
_sc2 = st.columns([2, 1, 2, 2, 2])
_sc2[0].metric(t("common.host"),     db_config["host"])
_sc2[1].metric(t("common.port"),     str(db_config["port"]))
_sc2[2].metric(t("common.database"), db_config["database"])
_sc2[3].metric(t("common.status"),   t("common.connected") if db_status["connected"] else t("common.failed"))
with _sc2[4]:
    st.write("")
    if st.button(
        t("model.reconnect"),
        key="btn_db_reconnect",
        use_container_width=True,
    ):
        st.session_state.loaded_data = None
        st.session_state.loaded_data_key = None
        st.rerun()
if not db_status["connected"]:
    _show_error(t("common.db_error"), db_status["message"])

st.divider()

# ---------------------------------------------------------------------------
# Общие параметры (Symbol / Timeframe)
# ---------------------------------------------------------------------------

st.subheader(t("model.params"))
_pc = st.columns([2, 2])
symbol    = _pc[0].text_input(t("common.symbol"), value="BTCUSDT", key="m_symbol",
                               on_change=_save_all_prefs)
_tf_keys  = list(builder.TIMEFRAMES.keys())
_tf_saved = st.session_state.get("m_tf", "60m")
_tf_idx   = _tf_keys.index(_tf_saved) if _tf_saved in _tf_keys else _tf_keys.index("60m")
timeframe = _pc[1].selectbox(
    t("common.timeframe"),
    options=_tf_keys,
    index=_tf_idx,
    key="m_tf",
    on_change=_save_all_prefs,
)

_, step_ms = builder.TIMEFRAMES[timeframe]
bars_per_year = int(_SECONDS_PER_YEAR * 1000 / step_ms)
sym_upper = symbol.upper().strip()
tbl_name  = builder.make_table_name(sym_upper, timeframe)

# Селектор целевой переменной
_target_candidates = (
    _fetch_target_candidates(db_config, tbl_name) if db_status["connected"] else []
)
if not _target_candidates:
    _target_candidates = [TARGET_COLUMN]
_prev_target = st.session_state.get("m_target", TARGET_COLUMN)
_target_index = (
    _target_candidates.index(_prev_target) if _prev_target in _target_candidates else 0
)
target_col = st.selectbox(
    t("model.target_col"),
    options=_target_candidates,
    index=_target_index,
    key="m_target",
    on_change=_save_all_prefs,
    help=t("model.target_help"),
)

# roadmap #2 — индикатор наличия данных в таблице
def _table_exists_quick(cfg: dict, table: str) -> bool:
    try:
        conn = connect_db(cfg)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=%s", (table,)
            )
            return cur.fetchone() is not None
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass

_tbl_exists = db_status["connected"] and _table_exists_quick(db_config, tbl_name)
_ic = st.columns(4)
_ic[0].info(f"`{tbl_name}`")
_ic[1].info(f"{t('model.bars_year')}: {bars_per_year:,}")
_ic[2].info(f"`{target_col}`")
if _tbl_exists:
    _ic[3].success(t("model.table_exists"))
else:
    _ic[3].warning(t("model.table_missing"))

# CV-режим
with st.expander(t("model.cv_expander"), expanded=False):
    _cv_cols = st.columns([2, 2, 3])
    cv_mode = _cv_cols[0].selectbox(
        t("model.cv_mode"),
        options=["expanding", "rolling"],
        index=(0 if st.session_state.get("m_cv_mode", "expanding") == "expanding" else 1),
        key="m_cv_mode",
        on_change=_save_all_prefs,
    )
    _mts_default = int(st.session_state.get("m_max_train_size", 0))
    max_train_size_val = int(_cv_cols[1].number_input(
        t("model.max_train_size"),
        min_value=0, max_value=10_000_000,
        value=_mts_default,
        step=500,
        key="m_max_train_size",
        on_change=_save_all_prefs,
        help=t("model.max_train_help"),
        disabled=(cv_mode != "rolling"),
    ))
    if cv_mode == "rolling" and max_train_size_val > 0:
        _cv_cols[2].info(t("model.rolling_info", n=f"{max_train_size_val:,}",
                            d=max_train_size_val * step_ms / (1000 * 86400)))
    else:
        _cv_cols[2].info(t("model.expanding_info"))

_max_train_size: int | None = max_train_size_val if (cv_mode == "rolling" and max_train_size_val > 0) else None

# Кеш датасетов
with st.expander(t("model.cache_expander"), expanded=False):
    _cc = st.columns([2, 2, 3])
    use_disk_cache_val = _cc[0].checkbox(
        t("model.use_cache"),
        value=bool(st.session_state.get("m_use_disk_cache", True)),
        key="m_use_disk_cache",
        on_change=_save_all_prefs,
        help=t("model.use_cache_help"),
    )
    if _cc[1].button(t("model.clear_cache"), key="m_clear_cache_btn"):
        _n = clear_cache()
        st.session_state.loaded_data = None
        st.session_state.loaded_data_key = None
        st.toast(t("model.cache_cleared", n=_n), icon="🗑")  # roadmap #14
        st.rerun()
    _stats = cache_stats()
    _mb = _stats["total_bytes"] / (1024 * 1024)
    _cc[2].info(t("model.cache_stats", n=_stats["n_files"], mb=_mb))
    if _stats["entries"]:
        _rows = []
        for e in sorted(_stats["entries"], key=lambda x: x.get("cached_at") or 0, reverse=True):
            _ts = e.get("cached_at")
            _ts_str = (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(_ts))
                if _ts else "—"
            )
            _rows.append({
                t("common.table"):  e.get("table") or "—",
                t("common.target"): e.get("target") or "—",
                t("common.rows"):   e.get("n_rows") or 0,
                "MB":               round(e["bytes"] / (1024 * 1024), 2),
                "cached_at":        _ts_str,
            })
        st.dataframe(pd.DataFrame(_rows), width="stretch", hide_index=True)

# Временной диапазон
_dc = st.columns([1, 1, 2])

def _iso_to_date(s: "str | None") -> "_dt.date | None":
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(str(s).split(" ")[0])
    except (ValueError, TypeError):
        return None

_date_from_raw = _dc[0].date_input(
    t("common.date_from"),
    value=_iso_to_date(st.session_state.get("m_date_from_iso")),
    key="m_date_from",
    on_change=_save_all_prefs,
    help=t("model.date_from_help"),
)
_date_to_raw = _dc[1].date_input(
    t("common.date_to"),
    value=_iso_to_date(st.session_state.get("m_date_to_iso")),
    key="m_date_to",
    on_change=_save_all_prefs,
    help=t("model.date_to_help"),
)
_date_from_str: str | None = (
    _date_from_raw.isoformat() if isinstance(_date_from_raw, _dt.date) else None
)
_date_to_str: str | None = (
    (_date_to_raw.isoformat() + " 23:59:59+00") if isinstance(_date_to_raw, _dt.date) else None
)
st.session_state["m_date_from_iso"] = _date_from_str
st.session_state["m_date_to_iso"]   = _date_to_str
if _date_from_str or _date_to_str:
    _range_label = (
        f"{_date_from_str or '…'}  →  {_date_to_raw.isoformat() if _date_to_raw else '…'}"
    )
    _dc[2].info(t("model.range_label", r=_range_label))
else:
    _dc[2].info(t("model.range_all"))

# Сбрасываем кеш данных при смене символа/таймфрейма/DB/дат/target
_curr_data_key = (
    sym_upper, timeframe,
    db_config["host"], str(db_config["port"]), db_config["database"],
    str(_date_from_str), str(_date_to_str),
    target_col,
)
if st.session_state.loaded_data_key != _curr_data_key:
    st.session_state.loaded_data = None

st.divider()

# ---------------------------------------------------------------------------
# Восстановление состояния после перезагрузки страницы
# st.session_state очищается при F5; восстанавливаем из файлов на диске
# ---------------------------------------------------------------------------

prefix = f"catboost_{sym_upper.lower()}_{timeframe.lower()}"
_restore_key = f"_restored_{prefix}"
if not st.session_state.get(_restore_key):
    st.session_state[_restore_key] = True
    # Grid Search
    if (
        st.session_state.grid_result is None
        or st.session_state.grid_result.get("prefix") != prefix
    ):
        _gr = load_grid_session_result(prefix)
        if _gr is not None:
            st.session_state.grid_result = _gr
    # Optuna Search
    if (
        st.session_state.optuna_result is None
        or st.session_state.optuna_result.get("prefix") != prefix
    ):
        _or = load_optuna_session_result(prefix)
        if _or is not None:
            st.session_state.optuna_result = _or
    # Результаты обучения
    if (
        st.session_state.model_result is None
        or st.session_state.model_result.get("prefix") != prefix
    ):
        _mr = load_session_result(prefix)
        if _mr is not None:
            st.session_state.model_result = _mr
            st.session_state.model_symbol    = sym_upper
            st.session_state.model_timeframe = timeframe

# ---------------------------------------------------------------------------
# Блок информации о датасете / предзагрузка
# ---------------------------------------------------------------------------

_ld = st.session_state.loaded_data
if _ld is not None and st.session_state.loaded_data_key == _curr_data_key:
    # Датасет уже загружен — показываем краткую сводку
    _all_ts = _ld["timestamps"]
    st.success(
        f"Датасет загружен: **{_ld['table_name']}**  ·  "
        f"{_all_ts.iloc[0].date()} → {_all_ts.iloc[-1].date()}  ·  "
        f"Всего: **{len(_ld['X']):,}** баров  ·  "
        f"Train: **{_ld['train_size']:,}** / Test: **{_ld['test_size']:,}**  ·  "
        f"Признаков: **{len(_ld['feature_cols'])}**"
    )
else:
    _dl_cols = st.columns([3, 1])
    _dl_cols[0].info(t("model.no_data_info"))
    if _dl_cols[1].button(
        t("model.btn_load"),
        key="btn_preload_data",
        disabled=not db_status["connected"],
        use_container_width=True,
    ):
        with st.spinner(t("model.loading")):
            try:
                _ld = _load_and_cache_data_auto(
                    db_config, sym_upper, timeframe, _date_from_str, _date_to_str,
                    target_col=target_col,
                )
                st.rerun()
            except Exception as _exc:
                _show_error(t("model.load_error"), _exc)

st.divider()

# ---------------------------------------------------------------------------
# TABS
# ---------------------------------------------------------------------------

tab_grid, tab_optuna, tab_train, tab_registry = st.tabs([
    t("model.tab_grid"), t("model.tab_optuna"),
    t("model.tab_train"), t("model.tab_registry"),
])

# ==========================================================================
# TAB 1 — Half Grid Search
# ==========================================================================

with tab_grid:
    st.subheader("Параметры Grid Search")
    st.caption(
        "Укажите допустимые значения для каждого параметра через запятую. "
        "Система перебирает все комбинации; если их больше лимита — делает случайную выборку."
    )

    _reset_btn = st.button("↺ Сбросить к умолчаниям", key="btn_reset_grid")
    if _reset_btn:
        st.session_state.pop("_grid_df_storage", None)
        st.session_state.pop("grid_max_combos", None)
        clear_grid_params_config()
        st.rerun()

    # Инициализация хранилища DataFrame — один раз за сессию.
    # Всегда передаём _grid_df_storage как data, чтобы Enter/rerun не сбрасывал значения.
    if "_grid_df_storage" not in st.session_state:
        _saved_gp = load_grid_params_config()
        if _saved_gp and _saved_gp.get("param_values"):
            _sv = _saved_gp["param_values"]
            _init_df = pd.DataFrame([
                {"параметр": k, "значения": _sv.get(k, ", ".join(str(v) for v in vs))}
                for k, vs in DEFAULT_PARAM_VALUES.items()
            ])
            if "grid_max_combos" not in st.session_state and "max_combos" in _saved_gp:
                st.session_state["grid_max_combos"] = int(_saved_gp["max_combos"])
        else:
            _init_df = _default_values_df()
        st.session_state["_grid_df_storage"] = _init_df

    edited_values_df = st.data_editor(
        st.session_state["_grid_df_storage"],
        num_rows="fixed",
        width="stretch",
        column_config=_VALUES_COL_CFG,
    )
    # Сохраняем полный текущий DataFrame — защита от сброса при следующем рендере
    st.session_state["_grid_df_storage"] = edited_values_df

    # Предпросмотр числа комбинаций
    _pv_preview: dict[str, list] | None = None
    try:
        _pv_preview = _parse_param_values(edited_values_df)
    except Exception:
        _pv_preview = None

    _lc = st.columns([1, 1, 2, 1])
    max_combos_val = _lc[0].number_input(
        "Лимит комбинаций (half-grid)",
        min_value=1, max_value=500,
        value=int(st.session_state.get("grid_max_combos", 10)),
        step=1,
        key="grid_max_combos",
    )
    grid_use_gpu = _lc[1].checkbox("GPU (CUDA)", value=True, key="grid_gpu")
    _total_combos = 0 if _pv_preview is None else _count_effective_combos(_pv_preview, use_gpu=grid_use_gpu)
    _actual = min(_total_combos, int(max_combos_val))
    _lc[2].info(
        f"Полный декарт: **{_total_combos}** комбинаций  →  "
        f"будет прогнано: **{_actual}**"
    )
    _save_params_clicked = _lc[3].button("💾 Сохранить параметры", key="btn_save_grid_params")
    if _save_params_clicked:
        try:
            _pv_s = _parse_param_values(edited_values_df)
            save_grid_params_config(
                {k: ", ".join(str(v) for v in vs) for k, vs in _pv_s.items()},
                int(max_combos_val),
            )
            st.toast("Параметры Grid Search сохранены ✓", icon="💾")
        except Exception as _e:
            _show_error("Ошибка сохранения параметров Grid", _e)

    # --- Статус фонового потока Grid Search ---
    _grid_alive  = is_thread_alive(f"{prefix}_grid")
    _grid_status = read_status(MODELS_DIR, prefix, "grid")
    # Если сервер перезапущен, а статус завис в "running" — сбросить
    if not _grid_alive and _grid_status and _grid_status.get("status") == "running":
        clear_status(MODELS_DIR, prefix, "grid")
        _grid_status = None
    _grid_running = _grid_alive

    run_grid_clicked = st.button(
        "▶ Запустить Grid Search",
        type="primary",
        width="stretch",
        disabled=_grid_running or not db_status["connected"],
        key="btn_run_grid",
    )

    if run_grid_clicked and not _grid_running and db_status["connected"]:
        save_local_config(db_config)
        _save_all_prefs()

        with st.spinner("Загрузка данных..."):
            try:
                data = _load_and_cache_data_auto(
                    db_config, sym_upper, timeframe, _date_from_str, _date_to_str,
                    target_col=target_col,
                )
            except Exception as exc:
                _show_error(t("model.load_error_grid"), exc)
                st.stop()

        _ts_all = data["timestamps"]
        st.info(
            f"Загружено **{len(data['X']):,}** баров  ·  "
            f"**{_ts_all.iloc[0].date()}** → **{_ts_all.iloc[-1].date()}**  ·  "
            f"Train: **{data['train_size']:,}** (до {data['timestamps'].iloc[data['train_size'] - 1].date()})  ·  "
            f"Test: **{data['test_size']:,}** (с {data['timestamps'].iloc[data['train_size']].date()})"
        )

        X_tr = data["X"].iloc[:data["train_size"]]
        y_tr = data["y"].iloc[:data["train_size"]]

        try:
            param_values = _parse_param_values(edited_values_df)
            effective_values = _effective_param_values(param_values, use_gpu=grid_use_gpu)
            custom_pg = expand_param_grid(effective_values, max_combos=int(max_combos_val))
        except Exception as exc:
            _show_error("Ошибка разбора параметров", exc)
            st.stop()

        if not custom_pg:
            _show_error("Нет комбинаций — проверьте значения параметров.", "пустой param_grid")
            st.stop()

        start_grid_search(
            prefix, X_tr, y_tr, custom_pg,
            use_gpu=grid_use_gpu,
            models_dir=MODELS_DIR,
            annualize_factor=float(bars_per_year),
            step_ms=step_ms,
            cv_mode=cv_mode,
            max_train_size=_max_train_size,
        )
        st.rerun()

    # --- Прогресс / результат фонового Grid Search ---
    if _grid_running:
        _gs = _grid_status or {}
        _cur = _gs.get("current", 0)
        _tot = max(_gs.get("total", 1), 1)
        st.progress(_cur / _tot, text=f"Grid Search: {_cur} / {_tot}")
        _bf = _gs.get("best_so_far", {})
        if _bf:
            _pc4 = st.columns(4)
            _pc4[0].metric("Лучший Sharpe",  f"{float(_bf.get('sharpe', 0)):.4f}")
            _pc4[1].metric("RMSE",            f"{float(_bf.get('mean_rmse_cv', 0)):.6f}")
            _pc4[2].metric("Dir.Acc%",        f"{float(_bf.get('dir_acc_pct', 0)):.1f}%")
            _pc4[3].metric("Profit Factor",   f"{float(_bf.get('profit_factor', 0)):.4f}")
        _hist = _gs.get("history") or []
        if _hist:
            st.plotly_chart(
                build_live_progress_chart(
                    _hist, title="Grid Search — live progress", x_label="combo #",
                ),
                use_container_width=True,
                key=f"grid_live_{prefix}_{_cur}",
            )
        time.sleep(1)
        st.rerun()
    elif _grid_status and _grid_status.get("status") == "done":
        # Авто-загрузка результатов если ещё не в session_state
        if (
            st.session_state.grid_result is None
            or st.session_state.grid_result.get("prefix") != prefix
        ):
            _gr_loaded = load_grid_session_result(prefix)
            if _gr_loaded is not None:
                st.session_state.grid_result = _gr_loaded
    elif _grid_status and _grid_status.get("status") == "error":
        _show_error(
            "Grid Search завершился с ошибкой",
            _grid_status.get("error_msg", "—"),
            tb=_grid_status.get("traceback"),
        )

    # --- Результаты ---
    if st.session_state.grid_result is not None:
        gr = st.session_state.grid_result
        st.divider()
        st.subheader("ТОП-10 Grid Search (Sharpe ↓)")
        st.caption(
            "Сортировка по Sharpe Ratio ↓, затем по RMSE ↑. "
            "TP/TN/FP/FN агрегированы по всем CV-фолдам пер комбинации."
        )

        _render_grid_results_table(gr["grid_df"], gr["best_params"])

        # Матрица ошибок для лучшей комбинации
        _best_row = gr["grid_df"].iloc[0]
        if "TP" in _best_row:
            st.divider()
            st.subheader("Матрица ошибок — лучшая комбинация")
            _render_confusion_metrics(_best_row)

        st.divider()

        # Кнопка сохранения лучшей модели
        if st.button(
            "💾 Сохранить лучшую модель (Grid Search)",
            key="btn_save_grid_model",
            disabled=not db_status["connected"] and st.session_state.loaded_data is None,
        ):
            # Если данные не загружены — загружаем автоматически
            if st.session_state.loaded_data is None or st.session_state.loaded_data_key != _curr_data_key:
                with st.spinner("Загрузка данных..."):
                    try:
                        st.session_state.loaded_data = _load_and_cache_data_auto(
                            db_config, sym_upper, timeframe, _date_from_str, _date_to_str,
                            target_col=target_col,
                        )
                    except Exception as _exc:
                        _show_error(t("model.load_error_grid"), _exc)
                        st.stop()

            if st.session_state.loaded_data is not None:
                d = st.session_state.loaded_data
                X_tr_s = d["X"].iloc[:d["train_size"]]
                y_tr_s = d["y"].iloc[:d["train_size"]]
                X_te_s = d["X"].iloc[d["train_size"]:]
                y_te_s = d["y"].iloc[d["train_size"]:]

                with st.spinner("Обучение и сохранение лучшей модели из Grid Search..."):
                    try:
                        _gmodel, _gmetrics, _gpred = train_final_model(
                            X_tr_s, y_tr_s,
                            X_te_s, y_te_s,
                            gr["best_params"],
                            annualize_factor=float(bars_per_year),
                            use_gpu=grid_use_gpu,
                        )
                        MODELS_DIR.mkdir(parents=True, exist_ok=True)
                        _save_path = (
                            MODELS_DIR
                            / f"catboost_{sym_upper.lower()}_{timeframe.lower()}_grid_best.cbm"
                        )
                        _gmodel.save_model(str(_save_path))
                        save_results_json(
                            _gmetrics, gr["best_params"], _save_path,
                            annualize_factor=float(bars_per_year),
                            prefix=f"catboost_{sym_upper.lower()}_{timeframe.lower()}_grid_best",
                        )
                        save_predictions_json(
                            y_te_s, _gpred, d["timestamps"].iloc[d["train_size"]:],
                            metrics=_gmetrics,
                            best_params=gr["best_params"],
                            model_path=_save_path,
                            prefix=f"catboost_{sym_upper.lower()}_{timeframe.lower()}_grid_best",
                        )
                        st.success(f"Модель сохранена: `{_save_path}`")
                        _gdir = compute_direction_metrics(y_te_s.values, _gpred)
                        _mcols = st.columns(7)
                        _mcols[0].metric("MAE",       f"{_gmetrics['MAE']:.6f}")
                        _mcols[1].metric("RMSE",      f"{_gmetrics['RMSE']:.6f}")
                        _mcols[2].metric("R²",        f"{_gmetrics['R2']:.4f}")
                        _mcols[3].metric("Sharpe",    f"{_gmetrics.get('sharpe', 0):.4f}")
                        _mcols[4].metric("Dir.Acc%",  f"{_gmetrics.get('dir_acc_pct', 0):.1f}%")
                        _mcols[5].metric("MAE%",      f"{_gmetrics.get('mae_pct', 0):.4f}")
                        _mcols[6].metric("PF",        f"{_gmetrics.get('profit_factor', 0):.4f}")
                        _render_confusion_metrics(pd.Series(_gdir))
                    except Exception as exc:
                        _show_error("Ошибка обучения/сохранения Grid-модели", exc)

# ==========================================================================
# TAB 2 — Optuna Search (TPE)
# ==========================================================================

with tab_optuna:
    st.subheader("Optuna — байесовский подбор гиперпараметров")
    st.caption(
        "TPE-сэмплер предлагает параметры интеллектуально; обычно 30–100 trials "
        "дают результат сравнимый с полным Grid Search за меньшее время."
    )

    _oc = st.columns([1, 1, 2])
    optuna_n_trials = int(_oc[0].number_input(
        "Число trial-ов",
        min_value=5, max_value=500,
        value=int(st.session_state.get("optuna_n_trials", 30)),
        step=5,
        key="optuna_n_trials",
    ))
    optuna_use_gpu = _oc[1].checkbox("GPU (CUDA)", value=True, key="optuna_gpu")
    _oc[2].info(
        f"Диапазоны поиска: iterations ∈ [500, 10000] step 500, depth ∈ [4, 10], "
        f"learning_rate ∈ [0.005, 0.1] log, l2_leaf_reg ∈ [1, 10], "
        f"bagging_temperature ∈ [0, 2]"
        + (", border_count ∈ {128, 254}" if optuna_use_gpu else "")
    )

    # --- Статус фонового потока Optuna ---
    _optuna_alive  = is_thread_alive(f"{prefix}_optuna")
    _optuna_status = read_status(MODELS_DIR, prefix, "optuna")
    if not _optuna_alive and _optuna_status and _optuna_status.get("status") == "running":
        clear_status(MODELS_DIR, prefix, "optuna")
        _optuna_status = None
    _optuna_running = _optuna_alive

    run_optuna_clicked = st.button(
        "▶ Запустить Optuna Search",
        type="primary",
        width="stretch",
        disabled=_optuna_running or not db_status["connected"],
        key="btn_run_optuna",
    )

    if run_optuna_clicked and not _optuna_running and db_status["connected"]:
        save_local_config(db_config)
        _save_all_prefs()

        with st.spinner("Загрузка данных..."):
            try:
                data = _load_and_cache_data_auto(
                    db_config, sym_upper, timeframe, _date_from_str, _date_to_str,
                    target_col=target_col,
                )
            except Exception as exc:
                _show_error(t("model.load_error_optuna"), exc)
                st.stop()

        _ts_all = data["timestamps"]
        st.info(
            f"Загружено **{len(data['X']):,}** баров  ·  "
            f"**{_ts_all.iloc[0].date()}** → **{_ts_all.iloc[-1].date()}**  ·  "
            f"Train: **{data['train_size']:,}** (до {data['timestamps'].iloc[data['train_size'] - 1].date()})  ·  "
            f"Test: **{data['test_size']:,}** (с {data['timestamps'].iloc[data['train_size']].date()})"
        )

        X_tr = data["X"].iloc[:data["train_size"]]
        y_tr = data["y"].iloc[:data["train_size"]]

        try:
            start_optuna_search(
                prefix, X_tr, y_tr,
                n_trials=optuna_n_trials,
                use_gpu=optuna_use_gpu,
                models_dir=MODELS_DIR,
                annualize_factor=float(bars_per_year),
                step_ms=step_ms,
                cv_mode=cv_mode,
                max_train_size=_max_train_size,
            )
        except ImportError as _imp:
            _show_error(
                "Optuna не установлена. Выполните: pip install 'optuna>=3.6'",
                _imp,
            )
            st.stop()
        st.rerun()

    # --- Прогресс / результат фонового Optuna ---
    if _optuna_running:
        _os = _optuna_status or {}
        _cur = _os.get("current", 0)
        _tot = max(_os.get("total", 1), 1)
        st.progress(_cur / _tot, text=f"Optuna: trial {_cur} / {_tot}")
        _bf = _os.get("best_so_far", {})
        if _bf:
            _pc4 = st.columns(4)
            _pc4[0].metric("Лучший Sharpe",  f"{float(_bf.get('sharpe', 0)):.4f}")
            _pc4[1].metric("RMSE",            f"{float(_bf.get('mean_rmse_cv', 0)):.6f}")
            _pc4[2].metric("Dir.Acc%",        f"{float(_bf.get('dir_acc_pct', 0)):.1f}%")
            _pc4[3].metric("Profit Factor",   f"{float(_bf.get('profit_factor', 0)):.4f}")
        _hist = _os.get("history") or []
        if _hist:
            st.plotly_chart(
                build_live_progress_chart(
                    _hist, title="Optuna — live progress", x_label="trial #",
                ),
                use_container_width=True,
                key=f"optuna_live_{prefix}_{_cur}",
            )
        time.sleep(1)
        st.rerun()
    elif _optuna_status and _optuna_status.get("status") == "done":
        if (
            st.session_state.optuna_result is None
            or st.session_state.optuna_result.get("prefix") != prefix
        ):
            _or_loaded = load_optuna_session_result(prefix)
            if _or_loaded is not None:
                st.session_state.optuna_result = _or_loaded
    elif _optuna_status and _optuna_status.get("status") == "error":
        _show_error(
            "Optuna завершилась с ошибкой",
            _optuna_status.get("error_msg", "—"),
            tb=_optuna_status.get("traceback"),
        )

    # --- Результаты ---
    if st.session_state.optuna_result is not None:
        opr = st.session_state.optuna_result
        st.divider()
        st.subheader("ТОП-10 Optuna Trials (Sharpe ↓)")
        st.caption(
            "Сортировка по Sharpe Ratio ↓, затем по RMSE ↑. "
            "Колонка combo = порядковый номер trial-а Optuna."
        )
        _render_grid_results_table(opr["grid_df"], opr["best_params"])

        _best_row = opr["grid_df"].iloc[0]
        if "TP" in _best_row:
            st.divider()
            st.subheader("Матрица ошибок — лучший trial")
            _render_confusion_metrics(_best_row)


# ==========================================================================
# TAB 3 — Финальное обучение
# ==========================================================================

with tab_train:
    st.subheader("Полное обучение модели")
    st.caption(
        "Walk-forward split 70%/30%. Если Grid Search на вкладке 1 уже запущен, "
        "его результаты будут использованы автоматически."
    )

    # Показываем лучшие сохранённые параметры Grid Search для этого датасета
    _saved_best = load_grid_best_params(f"catboost_{sym_upper.lower()}_{timeframe.lower()}")
    if _saved_best is not None:
        _sbp = _saved_best.get("best_params", {})
        _sbm = _saved_best.get("best_metrics", {})
        _sba = _saved_best.get("saved_at", "")
        with st.expander(
            f"★ Лучшие параметры Grid Search для {sym_upper} {timeframe}  "
            f"(сохранено: {_sba})",
            expanded=True,
        ):
            if _sbp:
                _sbpc = st.columns(len(_sbp))
                for _col, (_k, _v) in zip(_sbpc, _sbp.items()):
                    _col.metric(_k, str(_v))
            if _sbm:
                st.caption(
                    "CV-метрики: "
                    + "  | ".join(
                        f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                        for k, v in _sbm.items()
                    )
                )
    else:
        st.info(
            "Лучшие параметры ещё не сохранены — запустите Grid Search на вкладке «1» для этого датасета."
        )

    _tc = st.columns([1, 1])
    train_use_gpu = _tc[0].checkbox("GPU (CUDA)", value=True, key="train_gpu")
    skip_grid     = _tc[1].checkbox("Пропустить Grid Search", value=False, key="m_skip_grid")

    _grid_info = (
        "пропускается" if skip_grid
        else ("← из вкладки 1" if st.session_state.grid_result is not None
              else f"{len(PARAM_GRID)} комбинаций × 5 folds")
    )
    st.info(f"Grid: {_grid_info}  |  Таблица: `{tbl_name}`  |  Баров/год: {bars_per_year:,}")

    # roadmap #13 — checkbox снаружи expander; поля URI/experiment только если включён
    from backend.model import mlflow_available as _mlflow_available
    _mlflow_ok = _mlflow_available()
    use_mlflow = st.checkbox(
        t("model.use_mlflow"),
        value=st.session_state.get("use_mlflow", False),
        disabled=not _mlflow_ok,
        key="use_mlflow",
        on_change=_save_all_prefs,
    )
    if use_mlflow and _mlflow_ok:
        with st.expander(t("model.mlflow_expander"), expanded=True):
            _mf_cols = st.columns([2, 2])
            mlflow_uri = _mf_cols[0].text_input(
                t("model.mlflow_uri"),
                value=st.session_state.get("mlflow_uri", "http://localhost:5000"),
                key="mlflow_uri",
                on_change=_save_all_prefs,
            )
            mlflow_experiment = _mf_cols[1].text_input(
                t("model.mlflow_exp"),
                value=st.session_state.get("mlflow_experiment", "ModelLine"),
                key="mlflow_experiment",
                on_change=_save_all_prefs,
            )
            from urllib.parse import urlparse as _urlparse
            _parsed = _urlparse(mlflow_uri.strip())
            _uri_valid = _parsed.scheme in ("http", "https") and bool(_parsed.netloc)
            if not _uri_valid:
                st.error(t("model.invalid_uri"))
            else:
                st.caption(f"→ experiment **{mlflow_experiment}** @ `{mlflow_uri}`")
    else:
        if not _mlflow_ok:
            st.caption("`mlflow` не установлен. `pip install mlflow`")
        mlflow_uri = st.session_state.get("mlflow_uri", "http://localhost:5000")
        mlflow_experiment = st.session_state.get("mlflow_experiment", "ModelLine")

    # --- Статус фонового потока обучения ---
    _train_alive  = is_thread_alive(f"{prefix}_train")
    _train_status = read_status(MODELS_DIR, prefix, "train")
    # Если сервер перезапущен, а статус завис в "running" — сбросить
    if not _train_alive and _train_status and _train_status.get("status") == "running":
        clear_status(MODELS_DIR, prefix, "train")
        _train_status = None
    _train_running = _train_alive

    _btn_row = st.columns([2, 1, 1])
    train_clicked = _btn_row[0].button(
        "🚀 Запустить обучение",
        width="stretch",
        disabled=_train_running or not db_status["connected"],
        type="primary",
        key="btn_train",
    )
    save_full_clicked = _btn_row[1].button(
        "💾 Сохранить модель",
        width="stretch",
        disabled=(st.session_state.model_result is None),
        key="btn_save_full",
    )
    _pdf_placeholder = _btn_row[2].empty()
    if st.session_state.model_result is not None:
        _r_for_pdf = st.session_state.model_result
        try:
            _pdf_bytes = generate_session_pdf_bytes(
                prefix=_r_for_pdf["prefix"],
                model=_r_for_pdf["model"],
                metrics=_r_for_pdf["metrics"],
                best_params=_r_for_pdf["best_params"],
                feature_cols=_r_for_pdf["feature_cols"],
                y_test=_r_for_pdf["y_test"],
                y_pred=_r_for_pdf["y_pred"],
                ts_test=_r_for_pdf.get("ts_test"),
                target_col=_r_for_pdf.get("target_col") or TARGET_COLUMN,
                overfit_diagnostics=_r_for_pdf.get("overfit_diagnostics"),
            )
            _pdf_placeholder.download_button(
                "📄 Скачать PDF-отчёт",
                data=_pdf_bytes,
                file_name=f"{_r_for_pdf['prefix']}_report.pdf",
                mime="application/pdf",
                width="stretch",
                key="btn_download_pdf",
            )
        except ImportError as _pdf_imp:
            _pdf_placeholder.button(
                "📄 PDF (matplotlib?)",
                width="stretch", disabled=True,
                help=str(_pdf_imp),
                key="btn_download_pdf_disabled",
            )
        except Exception as _pdf_exc:
            _pdf_placeholder.button(
                "📄 PDF ошибка",
                width="stretch", disabled=True,
                help=f"{type(_pdf_exc).__name__}: {_pdf_exc}",
                key="btn_download_pdf_error",
            )
    else:
        _pdf_placeholder.button(
            "📄 Скачать PDF",
            width="stretch", disabled=True,
            key="btn_download_pdf_empty",
        )

    # --- Обучение ---
    if train_clicked and not _train_running and db_status["connected"]:
        # Проверка MLflow URI до запуска
        if st.session_state.get("use_mlflow", False):
            from urllib.parse import urlparse as _urlparse_check
            _uri_check = st.session_state.get("mlflow_uri", "").strip()
            _p = _urlparse_check(_uri_check)
            if not (_p.scheme in ("http", "https") and _p.netloc):
                st.error(f"Неверный MLflow Tracking URI: `{_uri_check}`. Исправьте в секции MLflow выше.")
                st.stop()
        save_local_config(db_config)
        _save_all_prefs()

        with st.spinner("Загрузка данных..."):
            try:
                data = _load_and_cache_data_auto(
                    db_config, sym_upper, timeframe, _date_from_str, _date_to_str,
                    target_col=target_col,
                )
            except Exception as exc:
                _show_error(t("model.load_error_train"), exc)
                st.stop()

        d = data
        _ts2 = d["timestamps"]
        st.info(
            f"Загружено **{len(d['X']):,}** баров  ·  "
            f"**{_ts2.iloc[0].date()}** → **{_ts2.iloc[-1].date()}**  ·  "
            f"Train: **{d['train_size']:,}** (до {_ts2.iloc[d['train_size'] - 1].date()})  ·  "
            f"Test: **{d['test_size']:,}** (с {_ts2.iloc[d['train_size']].date()})  ·  "
            f"Признаков: **{len(d['feature_cols'])}**"
        )

        X_train_t = d["X"].iloc[:d["train_size"]]
        y_train_t = d["y"].iloc[:d["train_size"]]
        X_test_t  = d["X"].iloc[d["train_size"]:]
        y_test_t  = d["y"].iloc[d["train_size"]:]
        ts_test   = d["timestamps"].iloc[d["train_size"]:]

        annualize = float(bars_per_year)

        # Определяем параметры: skip / из session_state / grid search в потоке
        if skip_grid:
            _prior_params: dict | None = PARAM_GRID[0].copy()
            _pg_for_thread: list[dict] | None = None
        elif st.session_state.grid_result is not None:
            _prior_params = st.session_state.grid_result["best_params"]
            _pg_for_thread = None
            st.info("Используются результаты Grid Search из вкладки 1.")
        elif st.session_state.optuna_result is not None:
            _prior_params = st.session_state.optuna_result["best_params"]
            _pg_for_thread = None
            st.info("Используются результаты Optuna Search из вкладки 2.")
        else:
            # Grid Search запустится внутри фонового потока
            _prior_params = None
            _pg_for_thread = _effective_param_grid(PARAM_GRID, use_gpu=train_use_gpu)

        start_training_pipeline(
            prefix,
            X_train_t, y_train_t,
            X_test_t,  y_test_t,
            ts_test,
            d["feature_cols"],
            prior_params=_prior_params,
            param_grid=_pg_for_thread,
            use_gpu=train_use_gpu,
            annualize_factor=annualize,
            step_ms=step_ms,
            models_dir=MODELS_DIR,
            target_col=target_col,
            cv_mode=cv_mode,
            max_train_size=_max_train_size,
            mlflow_enabled=st.session_state.get("use_mlflow", False),
            mlflow_uri=st.session_state.get("mlflow_uri", "http://localhost:5000"),
            mlflow_experiment=st.session_state.get("mlflow_experiment", "ModelLine"),
        )
        st.rerun()

    # --- Прогресс / результат фонового обучения ---
    if _train_running:
        _ts = _train_status or {}
        _phase = _ts.get("phase", "train")
        _phase_label = "Grid Search" if _phase == "grid" else "Обучение модели"
        st.info(f"⏳ {_phase_label} выполняется в фоне... Страницу можно перезагрузить — прогресс не потеряется.")
        time.sleep(2)
        st.rerun()
    elif _train_status and _train_status.get("status") == "done":
        # Авто-загрузка результатов если ещё не в session_state
        if (
            st.session_state.model_result is None
            or st.session_state.model_result.get("prefix") != prefix
        ):
            _mr_loaded = load_session_result(prefix)
            if _mr_loaded is not None:
                st.session_state.model_result = _mr_loaded
                st.session_state.model_symbol    = sym_upper
                st.session_state.model_timeframe = timeframe
        _mf_run_id = _train_status.get("mlflow_run_id")
        if _mf_run_id:
            st.success(f"✅ Обучение завершено. MLflow run_id: `{_mf_run_id}`")
        else:
            st.success("✅ Обучение завершено.")
        st.page_link("pages/compare_page.py", label="Открыть Compare — сравнение сессий →", icon="📊")
    elif _train_status and _train_status.get("status") == "error":
        _show_error(
            "Обучение завершилось с ошибкой",
            _train_status.get("error_msg", "—"),
            tb=_train_status.get("traceback"),
        )

    # --- Сохранение ---
    if save_full_clicked and st.session_state.model_result is not None:
        r = st.session_state.model_result
        path = save_model(r["model"], st.session_state.model_symbol, st.session_state.model_timeframe)
        save_results_json(
            r["metrics"], r["best_params"], path,
            annualize_factor=float(bars_per_year),
            prefix=r["prefix"],
        )
        save_predictions_json(
            r["y_test"], r["y_pred"], r.get("ts_test"),
            metrics=r["metrics"],
            best_params=r["best_params"],
            model_path=path,
            prefix=r["prefix"],
        )
        st.success(f"Модель сохранена: `{path}`")

    # --- Результаты ---
    if st.session_state.model_result is not None:
        r = st.session_state.model_result

        # Показываем на каком датасете обучена модель
        _r_ts = r.get("ts_test")
        _r_d  = st.session_state.loaded_data
        if _r_d is not None:
            _all_ts = _r_d["timestamps"]
            st.caption(
                f"Датасет обучения: **{_r_d['table_name']}**  ·  "
                f"{_all_ts.iloc[0].date()} → {_all_ts.iloc[-1].date()}  ·  "
                f"Train: {_r_d['train_size']:,} баров  ·  Test: {_r_d['test_size']:,} баров"
                + (f"  ·  Диапазон: {_r_d.get('date_from') or 'начало'} — {_r_d.get('date_to') or 'конец'}"
                   if _r_d.get('date_from') or _r_d.get('date_to') else "")
            )

        st.divider()
        st.subheader("Метрики на тестовой выборке (walk-forward 30%)")
        m = r["metrics"]
        _mc = st.columns(7)
        _mc[0].metric("MAE",      f"{m['MAE']:.6f}")
        _mc[1].metric("RMSE",     f"{m['RMSE']:.6f}")
        _mc[2].metric("R²",       f"{m['R2']:.4f}")
        _mc[3].metric("Sharpe",   f"{m.get('sharpe', 0):.4f}")
        _mc[4].metric("Dir.Acc%", f"{m.get('dir_acc_pct', 0):.1f}%")
        _mc[5].metric("MAE%",     f"{m.get('mae_pct', 0):.4f}")
        _mc[6].metric("PF",       f"{m.get('profit_factor', 0):.4f}")

        # Матрица ошибок на тесте
        st.divider()
        st.subheader("Матрица ошибок — тестовая выборка")
        st.caption("Положительный класс: рост цены (target_return_1 > 0)")
        _dir = compute_direction_metrics(r["y_test"].values, r["y_pred"])
        _render_confusion_metrics(pd.Series(_dir))

        # Проверка переобучения
        od = r.get("overfit_diagnostics")
        if od is not None:
            st.divider()
            st.subheader("Проверка переобучения")

            # 1. Learning curve
            lc = od["learning_curve"]
            if lc["iterations"]:
                st.markdown("**1. Learning Curve — Val RMSE по итерациям**")
                fig_lc = build_learning_curve_chart(
                    lc["iterations"],
                    lc["val_rmse"],
                    lc["train_rmse_at_best"],
                    lc["best_iteration"],
                )
                st.plotly_chart(fig_lc, width="stretch")

            # 2. R² gap
            st.markdown("**2. Train / Test R² — расхождение**")
            _ogc = st.columns(3)
            _ogc[0].metric("R² Train", f"{od['r2_train']:.4f}")
            _ogc[1].metric("R² Test",  f"{od['r2_test']:.4f}")
            _gap_sign = "+" if od["r2_gap_pct"] >= 0 else ""
            _ogc[2].metric("Gap (Train−Test)/|Train|", f"{_gap_sign}{od['r2_gap_pct']:.1f}%")
            if od["r2_overfit_flag"]:
                st.warning("⚠ R² gap превышает 20% — вероятно переобучение.")
            else:
                st.success("✓ R² gap в норме (<20%).")

            # 3. Walk-forward last month
            st.markdown(f"**3. Walk-forward — последние {od['wf_bars']} баров (~1 месяц)**")
            st.caption("Метрики на самых свежих барах тест-выборки (наибольшее временно́е смещение от тренировочных данных).")
            _wfc = st.columns(4)
            _wfc[0].metric("RMSE",     f"{od['wf_rmse']:.6f}")
            _wfc[1].metric("R²",       f"{od['wf_r2']:.4f}")
            _wfc[2].metric("Dir.Acc%", f"{od['wf_dir_acc_pct']:.1f}%")
            _wfc[3].metric("Sharpe",   f"{od['wf_sharpe']:.4f}")

            # 4. Feature importance concentration
            st.markdown("**4. Feature Importance — концентрация топ-5**")
            _fic = st.columns([1, 2])
            _fic[0].metric("Топ-5 сумма", f"{od['fi_top5_sum_pct']:.1f}%")
            _fic[1].table(
                pd.DataFrame({
                    "Признак":       od["fi_top5_names"],
                    "Важность (%)":  [f"{v:.2f}" for v in od["fi_top5_values"]],
                }).set_index("Признак")
            )
            if od["fi_concentration_flag"]:
                st.warning("⚠ Топ-5 признаков суммарно >30% — высокая концентрация.")
            else:
                st.success("✓ Важность не сконцентрирована в топ-5 (<30%).")

        # Лучшие гиперпараметры
        st.divider()
        st.subheader("Лучшие гиперпараметры")
        bp = r["best_params"]
        _bpc = st.columns(len(bp))
        for col, (k, v) in zip(_bpc, bp.items()):
            col.metric(k, str(v))

        # Actual vs Predicted
        st.divider()
        st.subheader("Actual vs Predicted")
        _tgt_label = r.get("target_col") or TARGET_COLUMN
        fig_avp = build_actual_vs_predicted_chart(
            r["y_test"], r["y_pred"], r["ts_test"], target_label=_tgt_label,
        )
        st.plotly_chart(fig_avp, width="stretch")

        # Cumulative P&L
        st.divider()
        st.subheader("Cumulative P&L — Strategy vs Buy & Hold")
        fig_pnl = build_cumulative_pnl_chart(r["y_test"], r["y_pred"], r["ts_test"])
        st.plotly_chart(fig_pnl, width="stretch")

        # Feature Importance
        st.divider()
        st.subheader("Feature Importance — TOP 20")
        importances = r["model"].get_feature_importance()
        fig_fi = build_feature_importance_chart(importances.tolist(), r["feature_cols"], top_n=20)
        st.plotly_chart(fig_fi, width="stretch")

        # SHAP summary — ленивое вычисление по кнопке
        st.divider()
        st.subheader("SHAP Summary — TOP 20")
        st.caption(
            "Средний |SHAP| по подвыборке до 2000 строк train-выборки. "
            "Отражает вклад каждого признака в предсказания модели."
        )
        _shap_key = f"_shap_{r['prefix']}"
        _shap_cached = st.session_state.get(_shap_key)
        _shap_disk = load_shap_summary(r["prefix"]) if _shap_cached is None else None

        _sc = st.columns([1, 3])
        _recalc_shap = _sc[0].button(
            "🔬 Рассчитать SHAP",
            key=f"btn_shap_{r['prefix']}",
            help="Пересчитать SHAP-значения для текущей модели",
        )
        if _shap_cached is not None:
            _sc[1].caption(f"Кэш: {_shap_cached['n_samples']:,} сэмплов, bias={_shap_cached['bias']:.6f}")
        elif _shap_disk is not None:
            _sc[1].caption(f"На диске: {len(_shap_disk):,} признаков (нажмите «Рассчитать SHAP» для детализации)")
        else:
            _sc[1].caption("SHAP ещё не рассчитан.")

        if _recalc_shap:
            _d_for_shap = st.session_state.loaded_data
            if _d_for_shap is None or _d_for_shap.get("train_size") is None:
                _show_error(
                    "Не удалось рассчитать SHAP",
                    "Датасет не загружен. Загрузите датасет выше и повторите.",
                )
            else:
                with st.spinner("Вычисление SHAP-значений..."):
                    try:
                        _X_shap = _d_for_shap["X"].iloc[:_d_for_shap["train_size"]]
                        _res = compute_shap_values(
                            r["model"], _X_shap, r["feature_cols"],
                            max_samples=2000,
                        )
                        st.session_state[_shap_key] = _res
                        save_shap_summary(_res, prefix=r["prefix"])
                        st.rerun()
                    except Exception as _exc:
                        _show_error("Ошибка расчёта SHAP", _exc)

        _shap_series = None
        if _shap_cached is not None:
            _shap_series = _shap_cached["mean_abs"]
        elif _shap_disk is not None:
            _shap_series = _shap_disk
        if _shap_series is not None and len(_shap_series) > 0:
            fig_shap = build_shap_summary_chart(_shap_series, top_n=20)
            st.plotly_chart(fig_shap, width="stretch")

        # Grid Search (если запускался в рамках финального обучения)
        if r["grid_df"] is not None:
            st.divider()
            st.subheader("Результаты Grid Search (финальное обучение)")
            _render_grid_results_table(r["grid_df"], r["best_params"])


# ==========================================================================
# TAB 4 — Реестр моделей
# ==========================================================================

with tab_registry:
    _reg_filter = st.text_input(
        "Filter by prefix",
        value="",
        key="reg_prefix_filter",
        placeholder="catboost_btcusdt_60m",
    )
    _reg_entries = load_registry(
        models_dir=MODELS_DIR,
        prefix_filter=_reg_filter.strip() or None,
        limit=100,
    )

    if not _reg_entries:
        st.info(t("model.registry_empty"))
    else:
        import pandas as _pd_reg

        def _fmt_dt(iso: str) -> str:
            try:
                return iso[:16].replace("T", " ")
            except Exception:
                return iso or "—"

        _reg_rows = []
        for _e in _reg_entries:
            _m = _e.get("metrics", {})
            _reg_rows.append({
                "version_id":    _e.get("version_id", "—"),
                "prefix":        _e.get("prefix", "—"),
                "trained_at":    _fmt_dt(_e.get("trained_at", "—")),
                "target_col":    _e.get("target_col") or "—",
                "n_train":       _e.get("n_train", 0),
                "n_test":        _e.get("n_test", 0),
                "n_features":    _e.get("n_features", 0),
                "sharpe":        _m.get("sharpe", float("nan")),
                "RMSE":          _m.get("RMSE", float("nan")),
                "dir_acc_pct":   _m.get("dir_acc_pct", float("nan")),
                "profit_factor": _m.get("profit_factor", float("nan")),
                "mlflow_run_id": _e.get("mlflow_run_id") or "—",
            })

        _reg_df = _pd_reg.DataFrame(_reg_rows)
        _fmt_reg = {
            "sharpe":        "{:.4f}",
            "RMSE":          "{:.6f}",
            "dir_acc_pct":   "{:.1f}",
            "profit_factor": "{:.4f}",
        }
        st.dataframe(
            _reg_df.style.format(_fmt_reg, na_rep="—"),
            use_container_width=True,
            hide_index=True,
        )

        # roadmap #10 — применить параметры лучшей версии в форму
        _reg_vids = [e["version_id"] for e in _reg_entries]
        _apply_vid = st.selectbox("version_id", _reg_vids, key="reg_apply_vid")
        _apply_entry = next((e for e in _reg_entries if e["version_id"] == _apply_vid), None)
        _rcols = st.columns([2, 1])
        if _apply_entry and _rcols[0].button(t("model.apply_params"), key="btn_reg_apply"):
            _bp = _apply_entry.get("best_params") or {}
            if _bp:
                # сохраняем в grid_params_config — подхватится при следующей загрузке страницы
                _str_vals = {k: str(v) for k, v in _bp.items()}
                save_grid_params_config(_str_vals, max_combos=1)
                st.session_state.pop("_grid_df_storage", None)
            st.toast(t("model.params_applied"), icon="✅")

        with _rcols[1].expander(t("model.delete_version"), expanded=False):
            _del_vid = st.text_input("version_id", key="reg_del_vid")
            if st.button(t("model.delete_version"), key="btn_reg_delete", type="primary",
                         disabled=not _del_vid.strip()):
                _ok = delete_registry_version(_del_vid.strip(), models_dir=MODELS_DIR)
                if _ok:
                    st.toast(t("model.version_deleted", v=_del_vid), icon="🗑")
                    st.rerun()
                else:
                    st.error(f"version_id `{_del_vid}` not found.")
