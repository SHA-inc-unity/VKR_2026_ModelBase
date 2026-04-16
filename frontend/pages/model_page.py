"""Страница обучения CatBoost-модели прогнозирования target_return_1."""
from __future__ import annotations

import sys
import time
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

from backend import dataset as builder
from backend.model import (
    compute_overfitting_diagnostics,
    grid_search_cv,
    load_training_data,
    save_model,
    train_final_model,
    walk_forward_split,
)
from backend.model.config import (
    DEFAULT_PARAM_VALUES,
    MODELS_DIR,
    PARAM_GRID,
    _PARAM_TYPES,
    expand_param_grid,
)
from backend.model.metrics import compute_direction_metrics, compute_trading_metrics
from backend.model.report import (
    load_grid_best_params,
    load_grid_session_result,
    load_session_result,
    save_grid_best_params,
    save_grid_results,
    save_results_json,
    save_session_result,
)
from services.trainer import (
    clear_status,
    is_thread_alive,
    read_status,
    start_grid_search,
    start_training_pipeline,
)
from services.db_auth import (
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


def _load_and_cache_data(
    config: dict, sym: str, tf: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Загружает данные из PostgreSQL и кеширует в session_state."""
    tbl = builder.make_table_name(sym, tf)
    data_key = (
        sym, tf,
        config["host"], str(config["port"]), config["database"],
        str(date_from), str(date_to),
    )
    if (
        st.session_state.get("loaded_data_key") == data_key
        and st.session_state.get("loaded_data") is not None
    ):
        return st.session_state.loaded_data  # type: ignore[return-value]

    conn = connect_db(config)
    try:
        X, y, feature_cols, timestamps = load_training_data(
            conn, tbl, date_from=date_from, date_to=date_to
        )
    finally:
        conn.close()

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
    }
    st.session_state.loaded_data = data
    st.session_state.loaded_data_key = data_key
    return data


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
        go.Bar(x=fi.values, y=fi.index.tolist(), orientation="h", marker_color="#4C72B0")
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


def build_actual_vs_predicted_chart(
    y_true: pd.Series,
    y_pred: np.ndarray,
    timestamps: pd.Series,
) -> go.Figure:
    """Временно́й график actual vs predicted (Plotly)."""
    y_pred_arr = np.asarray(y_pred)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=timestamps, y=y_true.values,
        mode="lines", name="Actual",
        line={"color": "#2196F3", "width": 1.2}, opacity=0.85,
    ))
    fig.add_trace(go.Scatter(
        x=timestamps, y=y_pred_arr,
        mode="lines", name="Predicted",
        line={"color": "#F44336", "width": 1.2}, opacity=0.85,
    ))
    fig.add_hline(y=0, line_color="gray", line_dash="dot", line_width=0.8)
    fig.update_layout(
        title="Actual vs Predicted — тестовая выборка",
        xaxis_title="Время", yaxis_title="target_return_1",
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
        line={"color": "#4CAF50", "width": 1.4},
    ))
    fig.add_trace(go.Scatter(
        x=timestamps, y=cum_bh,
        mode="lines", name="Buy & Hold",
        line={"color": "#2196F3", "width": 1.1, "dash": "dash"}, opacity=0.8,
    ))
    fig.add_hline(y=0, line_color="gray", line_dash="dot", line_width=0.7)
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
        line={"color": "#F44336", "width": 1.5},
    ))
    fig.add_hline(
        y=train_rmse_at_best,
        line_dash="dash", line_color="#4CAF50", line_width=1.2,
        annotation_text=f"Train RMSE = {train_rmse_at_best:.6f}",
        annotation_position="bottom right",
    )
    if best_iteration > 0:
        fig.add_vline(
            x=best_iteration,
            line_dash="dot", line_color="#FF9800", line_width=1.0,
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
    page_title="Model — CatBoost", layout="wide", initial_sidebar_state="collapsed"
)

if st.button("← Назад"):
    st.switch_page("app.py")

st.title("CatBoost — прогноз target_return_1")
st.caption(
    "Walk-forward split (70% train / 30% test) · "
    "Редактируемый Half-Grid · TimeSeriesSplit CV · GPU training"
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

for _k, _v in {
    "loaded_data":        None,
    "loaded_data_key":    None,
    "grid_result":        None,
    "model_result":       None,
    "model_symbol":       None,
    "model_timeframe":    None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ---------------------------------------------------------------------------
# DB подключение
# ---------------------------------------------------------------------------

restored_config = load_db_config(load_local_config())

with st.expander("Подключение к PostgreSQL", expanded=False):
    _ov = st.columns(5)
    ov_host     = _ov[0].text_input("Host",     value=restored_config["host"],      key="m_host")
    ov_port     = _ov[1].text_input("Port",     value=str(restored_config["port"]), key="m_port")
    ov_database = _ov[2].text_input("Database", value=restored_config["database"],  key="m_db")
    ov_user     = _ov[3].text_input("User",     value=restored_config["user"],      key="m_user")
    ov_password = _ov[4].text_input(
        "Password", value=restored_config["password"], type="password", key="m_pass"
    )
    _sc = st.columns(2)
    if _sc[0].button("Сохранить настройки", key="m_save"):
        save_local_config(load_db_config(
            {"host": ov_host, "port": ov_port, "database": ov_database,
             "user": ov_user, "password": ov_password}
        ))
        st.success("Настройки сохранены.")
    if _sc[1].button("Очистить настройки", key="m_clear"):
        clear_local_config()
        st.rerun()

db_config = load_db_config(
    {"host": ov_host, "port": ov_port, "database": ov_database,
     "user": ov_user, "password": ov_password}
)
db_status = probe_db_connection(db_config)

st.subheader("Статус базы данных")
_sc2 = st.columns(4)
_sc2[0].metric("Host",     db_config["host"])
_sc2[1].metric("Port",     str(db_config["port"]))
_sc2[2].metric("Database", db_config["database"])
_sc2[3].metric("Status",   "Connected" if db_status["connected"] else "Failed")
if not db_status["connected"]:
    st.error(f"Ошибка подключения: {db_status['message']}")

st.divider()

# ---------------------------------------------------------------------------
# Общие параметры (Symbol / Timeframe)
# ---------------------------------------------------------------------------

st.subheader("Параметры")
_pc = st.columns([2, 2])
symbol    = _pc[0].text_input("Symbol",    value="BTCUSDT", key="m_symbol")
timeframe = _pc[1].selectbox(
    "Timeframe",
    options=list(builder.TIMEFRAMES.keys()),
    index=list(builder.TIMEFRAMES.keys()).index("60m"),
    key="m_tf",
)

_, step_ms = builder.TIMEFRAMES[timeframe]
bars_per_year = int(_SECONDS_PER_YEAR * 1000 / step_ms)
sym_upper = symbol.upper().strip()
tbl_name  = builder.make_table_name(sym_upper, timeframe)

_ic = st.columns(2)
_ic[0].info(f"Таблица: `{tbl_name}`")
_ic[1].info(f"Баров/год: {bars_per_year:,}")

# Временной диапазон данных для обучения
_dc = st.columns([1, 1, 2])
_date_from_raw = _dc[0].date_input(
    "Дата начала (включительно)",
    value=None, key="m_date_from",
    help="Оставьте пустым — загрузить с начала таблицы",
)
_date_to_raw = _dc[1].date_input(
    "Дата конца (включительно)",
    value=None, key="m_date_to",
    help="Оставьте пустым — загрузить до конца таблицы",
)
# Конвертируем в строку ISO для SQL (или None)
import datetime as _dt
_date_from_str: str | None = (
    _date_from_raw.isoformat() if isinstance(_date_from_raw, _dt.date) else None
)
_date_to_str: str | None = (
    # Берём конец дня (23:59:59 UTC), чтобы включить все бары выбранной даты
    (_date_to_raw.isoformat() + " 23:59:59+00") if isinstance(_date_to_raw, _dt.date) else None
)
if _date_from_str or _date_to_str:
    _range_label = (
        f"{_date_from_str or '…'}  →  {_date_to_raw.isoformat() if _date_to_raw else '…'}"
    )
    _dc[2].info(f"Диапазон: **{_range_label}**")
else:
    _dc[2].info("Диапазон: **все данные из таблицы**")

# Сбрасываем кеш данных при смене символа/таймфрейма/DB/дат
_curr_data_key = (
    sym_upper, timeframe,
    db_config["host"], str(db_config["port"]), db_config["database"],
    str(_date_from_str), str(_date_to_str),
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
    # Данные не загружены — предлагаем загрузить заранее
    _dl_cols = st.columns([3, 1])
    _dl_cols[0].info(
        "Датасет ещё не загружен. "
        "Он будет загружен автоматически при запуске Grid Search или обучения, "
        "либо можно загрузить сейчас."
    )
    if _dl_cols[1].button(
        "⬇ Загрузить датасет",
        key="btn_preload_data",
        disabled=not db_status["connected"],
        use_container_width=True,
    ):
        with st.spinner("Загрузка данных из PostgreSQL..."):
            try:
                _ld = _load_and_cache_data(
                    db_config, sym_upper, timeframe, _date_from_str, _date_to_str
                )
                st.rerun()
            except Exception as _exc:
                st.error(f"Ошибка загрузки: {_exc}")

st.divider()

# ---------------------------------------------------------------------------
# TABS
# ---------------------------------------------------------------------------

tab_grid, tab_train = st.tabs(["Half Grid Search", "Финальное обучение"])

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
            st.error(f"Ошибка сохранения: {_e}")

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

        with st.spinner("Загрузка данных из PostgreSQL..."):
            try:
                data = _load_and_cache_data(
                    db_config, sym_upper, timeframe, _date_from_str, _date_to_str
                )
            except Exception as exc:
                st.error(f"Ошибка загрузки данных: {exc}")
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
            st.error(f"Ошибка разбора параметров: {exc}")
            st.stop()

        if not custom_pg:
            st.error("Нет комбинаций — проверьте значения параметров.")
            st.stop()

        start_grid_search(prefix, X_tr, y_tr, custom_pg, use_gpu=grid_use_gpu, models_dir=MODELS_DIR)
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
        st.error(f"Grid Search завершился с ошибкой: {_grid_status.get('error_msg', '—')}")

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
                with st.spinner("Загрузка данных из PostgreSQL..."):
                    try:
                        st.session_state.loaded_data = _load_and_cache_data(
                            db_config, sym_upper, timeframe, _date_from_str, _date_to_str
                        )
                    except Exception as _exc:
                        st.error(f"Ошибка загрузки данных: {_exc}")
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
                        st.error(f"Ошибка: {exc}")

# ==========================================================================
# TAB 2 — Финальное обучение
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

    # --- Статус фонового потока обучения ---
    _train_alive  = is_thread_alive(f"{prefix}_train")
    _train_status = read_status(MODELS_DIR, prefix, "train")
    # Если сервер перезапущен, а статус завис в "running" — сбросить
    if not _train_alive and _train_status and _train_status.get("status") == "running":
        clear_status(MODELS_DIR, prefix, "train")
        _train_status = None
    _train_running = _train_alive

    _btn_row = st.columns([2, 1])
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

    # --- Обучение ---
    if train_clicked and not _train_running and db_status["connected"]:
        save_local_config(db_config)

        with st.spinner("Загрузка данных из PostgreSQL..."):
            try:
                data = _load_and_cache_data(
                    db_config, sym_upper, timeframe, _date_from_str, _date_to_str
                )
            except Exception as exc:
                st.error(f"Ошибка загрузки данных: {exc}")
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
    elif _train_status and _train_status.get("status") == "error":
        st.error(f"Обучение завершилось с ошибкой: {_train_status.get('error_msg', '—')}")

    # --- Сохранение ---
    if save_full_clicked and st.session_state.model_result is not None:
        r = st.session_state.model_result
        path = save_model(r["model"], st.session_state.model_symbol, st.session_state.model_timeframe)
        save_results_json(
            r["metrics"], r["best_params"], path,
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
        fig_avp = build_actual_vs_predicted_chart(r["y_test"], r["y_pred"], r["ts_test"])
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

        # Grid Search (если запускался в рамках финального обучения)
        if r["grid_df"] is not None:
            st.divider()
            st.subheader("Результаты Grid Search (финальное обучение)")
            _render_grid_results_table(r["grid_df"], r["best_params"])
