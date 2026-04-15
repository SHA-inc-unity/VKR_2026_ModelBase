"""Окно загрузки и просмотра датасета Bybit."""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import psycopg2
import streamlit as st

# Пути: корень воркспейса (для builder) и каталог frontend (для services)
_HERE = Path(__file__).resolve()
_WORKSPACE_ROOT = _HERE.parents[2]
_FRONTEND_ROOT = _HERE.parents[1]
for _p in (_WORKSPACE_ROOT, _FRONTEND_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import build_market_dataset_to_postgres as builder
from services.db_auth import (
    clear_local_config,
    load_db_config,
    load_local_config,
    save_local_config,
)

PLOT_FIELDS = [
    "index_price",
    "funding_rate",
    "open_interest",
    "bid1_price",
    "ask1_price",
    "bid1_size",
    "ask1_size",
    "rsi",
]


def make_request(symbol: str, timeframe: str, start_date: date, end_date: date) -> dict:
    """Готовит параметры диапазона для проверки и загрузки."""
    symbol = symbol.upper().strip()
    timeframe, bybit_interval, step_ms = builder.normalize_timeframe(timeframe)
    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_dt = (
        datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        - timedelta(milliseconds=1)
    )
    start_ms, end_ms = builder.normalize_window(
        int(start_dt.timestamp() * 1000),
        int(end_dt.timestamp() * 1000),
        step_ms,
    )
    launch_time_ms, funding_lookback_ms = builder.fetch_instrument_details("linear", symbol)
    start_ms = max(start_ms, builder.ceil_to_step(launch_time_ms, step_ms))
    if start_ms > end_ms:
        raise RuntimeError("Requested range is before the instrument launch time")
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bybit_interval": bybit_interval,
        "step_ms": step_ms,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "funding_lookback_ms": funding_lookback_ms,
        "open_interest_interval": builder.choose_open_interest_interval(step_ms),
        "table_name": builder.make_table_name(symbol, timeframe),
    }


def connect_db(config: dict) -> psycopg2.extensions.connection:
    """Подключается к PostgreSQL по текущей demo-конфигурации."""
    params = {"host": config["host"], "port": config["port"], "dbname": config["database"]}
    if config.get("user"):
        params["user"] = config["user"]
    if config.get("password"):
        params["password"] = config["password"]
    return psycopg2.connect(**params)


def probe_db_connection(config: dict) -> dict:
    """Проверяет подключение и возвращает компактный статус для UI."""
    try:
        conn = connect_db(config)
    except Exception as exc:
        hint = ""
        if not config.get("user") or not config.get("password"):
            hint = " PGUSER/PGPASSWORD not set — local trust auth required."
        return {"connected": False, "message": f"{exc}{hint}"}
    conn.close()
    return {"connected": True, "message": ""}


def set_download_progress(
    progress_bar,
    status_placeholder,
    loaded_candles: int,
    total_candles: int,
) -> None:
    """Обновляет прогресс скачивания по числу загруженных свечей."""
    if progress_bar is None or status_placeholder is None:
        return
    total_value = max(int(total_candles), 1)
    loaded_value = max(0, min(int(loaded_candles), total_value))
    progress = loaded_value / total_value
    progress = max(0.0, min(progress, 1.0))
    percent = int(round(progress * 100))
    progress_bar.progress(progress)
    status_placeholder.text(f"Loaded: {loaded_value} / {total_value} candles ({percent}%)")


def check_coverage(config: dict, request_params: dict) -> dict:
    """Проверяет покрытие диапазона данными в PostgreSQL."""
    conn = connect_db(config)
    try:
        exists = builder.table_exists(conn, request_params["table_name"])
        existing_rows = (
            builder.fetch_db_rows(
                conn,
                request_params["table_name"],
                request_params["start_ms"],
                request_params["end_ms"],
            )
            if exists
            else {}
        )
    finally:
        conn.close()
    existing_ts = sorted(existing_rows)
    missing_ts = builder.find_missing_timestamps(
        set(existing_rows),
        request_params["start_ms"],
        request_params["end_ms"],
        request_params["step_ms"],
    )
    return {
        "table_name": request_params["table_name"],
        "exists": exists,
        "status": "full" if not missing_ts else ("empty" if not existing_ts else "partial"),
        "expected_count": len(
            range(
                request_params["start_ms"],
                request_params["end_ms"] + request_params["step_ms"],
                request_params["step_ms"],
            )
        ),
        "existing_count": len(existing_ts),
        "missing_count": len(missing_ts),
        "existing_ranges": builder.group_missing_ranges(existing_ts, request_params["step_ms"]),
        "missing_ranges": builder.group_missing_ranges(missing_ts, request_params["step_ms"]),
        "start_ms": request_params["start_ms"],
        "end_ms": request_params["end_ms"],
    }


def download_missing(
    config: dict,
    request_params: dict,
    rsi_period: int = 14,
    progress_bar=None,
    status_placeholder=None,
) -> dict:
    """Докачивает свечи и обновляет прогресс во время реального скачивания."""
    conn = connect_db(config)
    try:
        total_candles = (
            (request_params["end_ms"] - request_params["start_ms"]) // request_params["step_ms"] + 1
        )
        builder.ensure_table(conn, request_params["table_name"])
        requested_rows = builder.fetch_db_rows(
            conn, request_params["table_name"], request_params["start_ms"], request_params["end_ms"]
        )
        missing_requested = builder.find_missing_timestamps(
            set(requested_rows),
            request_params["start_ms"],
            request_params["end_ms"],
            request_params["step_ms"],
        )
        if not missing_requested:
            set_download_progress(
                progress_bar,
                status_placeholder,
                total_candles,
                total_candles,
            )
            return {"inserted": 0, "updated": 0, "downloaded_ranges": []}

        refresh_start = max(
            request_params["start_ms"],
            missing_requested[0] - rsi_period * request_params["step_ms"],
        )
        combined_rows = builder.fetch_db_rows(
            conn, request_params["table_name"], refresh_start, request_params["end_ms"]
        )
        missing_refresh = builder.find_missing_timestamps(
            set(combined_rows), refresh_start, request_params["end_ms"], request_params["step_ms"]
        )
        missing_ranges = builder.group_missing_ranges(missing_refresh, request_params["step_ms"])

        for range_start, range_end in missing_ranges:
            base_loaded_candles = sum(
                1
                for timestamp in combined_rows
                if request_params["start_ms"] <= timestamp <= request_params["end_ms"]
            )

            def report_range_progress(range_loaded_candles: int) -> None:
                """Передает частичный прогресс загрузки диапазона в Streamlit."""
                set_download_progress(
                    progress_bar,
                    status_placeholder,
                    base_loaded_candles + range_loaded_candles,
                    total_candles,
                )

            combined_rows.update(
                builder.fetch_range_rows(
                    "linear",
                    request_params["symbol"],
                    request_params["timeframe"],
                    request_params["bybit_interval"],
                    range_start,
                    range_end,
                    request_params["funding_lookback_ms"],
                    request_params["open_interest_interval"],
                    progress_callback=report_range_progress,
                    progress_start_ms=request_params["start_ms"],
                    progress_end_ms=request_params["end_ms"],
                )
            )
            loaded_candles = sum(
                1
                for timestamp in combined_rows
                if request_params["start_ms"] <= timestamp <= request_params["end_ms"]
            )
            set_download_progress(progress_bar, status_placeholder, loaded_candles, total_candles)

        ordered_ts = list(
            range(refresh_start, request_params["end_ms"] + request_params["step_ms"], request_params["step_ms"])
        )
        still_missing = [ts for ts in ordered_ts if ts not in combined_rows]
        if still_missing:
            raise RuntimeError(
                f"Bybit did not return full coverage for {len(still_missing)} timestamps "
                f"starting at {builder.ms_to_datetime(still_missing[0]).isoformat()}"
            )

        rows_to_write = [combined_rows[ts] for ts in ordered_ts]
        set_download_progress(
            progress_bar,
            status_placeholder,
            total_candles,
            total_candles,
        )
        builder.rebuild_rsi(rows_to_write, rsi_period)
        builder.validate_rows(rows_to_write, rsi_period)
        inserted, updated = builder.upsert_rows(conn, request_params["table_name"], rows_to_write)
        set_download_progress(
            progress_bar,
            status_placeholder,
            total_candles,
            total_candles,
        )
        return {"inserted": inserted, "updated": updated, "downloaded_ranges": missing_ranges}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_dataset(config: dict, request_params: dict) -> pd.DataFrame:
    """Загружает датасет из PostgreSQL в DataFrame."""
    conn = connect_db(config)
    try:
        if not builder.table_exists(conn, request_params["table_name"]):
            return pd.DataFrame()
        rows = builder.fetch_db_rows(
            conn, request_params["table_name"], request_params["start_ms"], request_params["end_ms"]
        )
    finally:
        conn.close()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame([rows[ts] for ts in sorted(rows)])
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
    return frame


def ranges_frame(ranges: list[tuple[int, int]]) -> pd.DataFrame:
    """Преобразует диапазоны в компактную таблицу для UI."""
    if not ranges:
        return pd.DataFrame(columns=["start", "end"])
    return pd.DataFrame(
        [
            {
                "start": builder.ms_to_datetime(s).isoformat(),
                "end": builder.ms_to_datetime(e).isoformat(),
            }
            for s, e in ranges
        ]
    )


def dataset_summary(frame: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Собирает краткую статистику по загруженному датасету."""
    if frame.empty:
        return {}, pd.DataFrame(columns=["column", "missing_values"])
    summary = {
        "row_count": int(len(frame)),
        "min_timestamp": frame["timestamp_utc"].min().isoformat(),
        "max_timestamp": frame["timestamp_utc"].max().isoformat(),
        "available_columns": list(frame.columns),
    }
    missing = frame.isna().sum().rename_axis("column").reset_index(name="missing_values")
    return summary, missing


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Dataset window", layout="wide", initial_sidebar_state="collapsed")

if st.button("Back to main"):
    st.switch_page("app.py")

st.title("Dataset window")
st.caption("Покрытие данных, загрузка пропусков, просмотр датасета и графики.")

# Загружаем сохранённую конфигурацию подключения до отрисовки полей ввода
restored_config = load_db_config(load_local_config())

# Session state
for _key, _default in (
    ("coverage", None),
    ("dataset", None),
    ("last_download", None),
    ("controls_signature", None),
):
    if _key not in st.session_state:
        st.session_state[_key] = _default
if st.session_state.dataset is None:
    st.session_state.dataset = pd.DataFrame()

# Выбор инструмента и диапазона
control_cols = st.columns(4)
symbol = control_cols[0].text_input("Symbol", value="BTCUSDT")
timeframe = control_cols[1].selectbox(
    "Timeframe",
    options=list(builder.TIMEFRAMES.keys()),
    index=list(builder.TIMEFRAMES.keys()).index("60m"),
)
start_date = control_cols[2].date_input("Start date", value=date(2024, 1, 1))
end_date = control_cols[3].date_input("End date", value=date.today())

# Ручной override с prefill из локального файла и env
with st.expander("Manual DB override (optional)", expanded=False):
    ov_cols = st.columns(5)
    ov_host = ov_cols[0].text_input("Host", value=restored_config["host"])
    ov_port = ov_cols[1].text_input("Port", value=str(restored_config["port"]))
    ov_database = ov_cols[2].text_input("Database", value=restored_config["database"])
    ov_user = ov_cols[3].text_input("User", value=restored_config["user"])
    ov_password = ov_cols[4].text_input(
        "Password", value=restored_config["password"], type="password"
    )
    save_cols = st.columns(2)
    if save_cols[0].button("Save connection settings"):
        save_local_config(
            load_db_config(
                {"host": ov_host, "port": ov_port, "database": ov_database, "user": ov_user, "password": ov_password}
            ),
        )
        st.success("Connection settings saved to .db_config.json.")
    if save_cols[1].button("Clear saved settings"):
        clear_local_config()
        st.rerun()

db_config = load_db_config(
    {"host": ov_host, "port": ov_port, "database": ov_database, "user": ov_user, "password": ov_password}
)
db_status = probe_db_connection(db_config)

st.subheader("Database status")
status_cols = st.columns(4)
status_cols[0].metric("Host", db_config["host"])
status_cols[1].metric("Port", str(db_config["port"]))
status_cols[2].metric("Database", db_config["database"])
status_cols[3].metric("Status", "Connected" if db_status["connected"] else "Failed")
if not db_status["connected"]:
    st.error(f"Database connection failed: {db_status['message']}")

# Сбрасываем состояние при изменении параметров
controls_signature = (
    symbol, timeframe,
    start_date.isoformat(), end_date.isoformat(),
    db_config["host"], str(db_config["port"]), db_config["database"], db_config.get("user", ""),
    db_config.get("password", ""),
)
if st.session_state.controls_signature != controls_signature:
    st.session_state.controls_signature = controls_signature
    st.session_state.coverage = None
    st.session_state.dataset = pd.DataFrame()
    st.session_state.last_download = None

button_cols = st.columns(3)
check_clicked = button_cols[0].button(
    "Check coverage", use_container_width=True, disabled=not db_status["connected"]
)
download_clicked = button_cols[1].button(
    "Download missing data", use_container_width=True, disabled=not db_status["connected"]
)
load_clicked = button_cols[2].button(
    "Load dataset", use_container_width=True, disabled=not db_status["connected"]
)

if start_date > end_date:
    st.error("Start date must not be later than end date.")
else:
    try:
        request_params = make_request(symbol, timeframe, start_date, end_date)
    except Exception as exc:
        request_params = None
        st.error(str(exc))

    if request_params and not db_status["connected"]:
        st.info("Set PostgreSQL environment variables or use the manual override above, then retry.")

    if request_params and db_status["connected"] and check_clicked:
        save_local_config(db_config)
        with st.spinner("Checking dataset coverage..."):
            st.session_state.coverage = check_coverage(db_config, request_params)

    if request_params and db_status["connected"] and download_clicked:
        save_local_config(db_config)
        download_progress_bar = st.progress(0)
        download_status = st.empty()
        try:
            st.session_state.last_download = download_missing(
                db_config, request_params,
                progress_bar=download_progress_bar,
                status_placeholder=download_status,
            )
            st.session_state.coverage = check_coverage(db_config, request_params)
            st.success("Download complete")
        except Exception as exc:
            download_status.text("Download failed")
            st.error(str(exc))

    if request_params and db_status["connected"] and load_clicked:
        save_local_config(db_config)
        with st.spinner("Loading dataset from PostgreSQL..."):
            st.session_state.dataset = load_dataset(db_config, request_params)

# Отображение покрытия
if st.session_state.coverage:
    cov = st.session_state.coverage
    st.subheader("Coverage")
    cov_cols = st.columns(5)
    cov_cols[0].metric("Table", cov["table_name"])
    cov_cols[1].metric("Status", cov["status"])
    cov_cols[2].metric("Expected", cov["expected_count"])
    cov_cols[3].metric("Existing", cov["existing_count"])
    cov_cols[4].metric("Missing", cov["missing_count"])
    iv_cols = st.columns(2)
    iv_cols[0].write("Existing intervals")
    iv_cols[0].dataframe(ranges_frame(cov["existing_ranges"]), use_container_width=True, hide_index=True)
    iv_cols[1].write("Missing intervals")
    iv_cols[1].dataframe(ranges_frame(cov["missing_ranges"]), use_container_width=True, hide_index=True)

# Результат последней загрузки
if st.session_state.last_download is not None:
    dl = st.session_state.last_download
    st.subheader("Download result")
    st.write(
        {
            "inserted_rows": dl["inserted"],
            "updated_rows": dl["updated"],
            "downloaded_ranges": ranges_frame(dl["downloaded_ranges"]).to_dict("records"),
        }
    )

# Просмотр датасета и графики
if not st.session_state.dataset.empty:
    dataset = st.session_state.dataset
    summary, missing = dataset_summary(dataset)
    st.subheader("Dataset summary")
    st.write(
        {
            "row_count": summary["row_count"],
            "min_timestamp": summary["min_timestamp"],
            "max_timestamp": summary["max_timestamp"],
            "available_columns": summary["available_columns"],
        }
    )
    st.dataframe(missing, use_container_width=True, hide_index=True)
    default_fields = [f for f in ["index_price", "rsi"] if f in dataset.columns]
    selected_fields = st.multiselect("Fields to plot", options=PLOT_FIELDS, default=default_fields)
    if selected_fields:
        chart_frame = dataset[["timestamp_utc", *selected_fields]].copy().set_index("timestamp_utc")
        st.line_chart(chart_frame)
    st.dataframe(dataset, use_container_width=True, hide_index=True)
