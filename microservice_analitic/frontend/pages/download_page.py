"""Страница загрузки и просмотра датасета Bybit."""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2 import sql
import streamlit as st

# Пути: корень воркспейса (для builder) и каталог frontend (для services)
_HERE = Path(__file__).resolve()
_WORKSPACE_ROOT = _HERE.parents[2]
_FRONTEND_ROOT = _HERE.parents[1]
for _p in (_WORKSPACE_ROOT, _FRONTEND_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backend import dataset as builder
from services.charts import get_plot_fields, render_charts
from services.db_auth import (
    clear_local_config,
    load_db_config,
    load_local_config,
    load_ui_prefs,
    save_local_config,
    save_ui_prefs,
)
from services.i18n import t
from services.ui_components import render_back_button, render_db_status, render_lang_toggle

_EXPECTED_TABLE_SCHEMA = builder.EXPECTED_TABLE_SCHEMA
_FORBIDDEN_TABLE_COLUMNS = builder.FORBIDDEN_TABLE_COLUMNS


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
        "launch_time_ms": launch_time_ms,
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


def _read_table_schema(connection: psycopg2.extensions.connection, table_name: str) -> list[tuple[str, str]]:
    """Читает текущую схему таблицы из information_schema."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]


def _create_market_table(
    connection: psycopg2.extensions.connection,
    table_name: str,
    if_not_exists: bool = False,
) -> None:
    """Создаёт таблицу датасета с ожидаемой схемой."""
    clause = "IF NOT EXISTS " if if_not_exists else ""
    column_defs = []
    for column_name, data_type in _EXPECTED_TABLE_SCHEMA:
        not_null = " NOT NULL" if column_name in {"timestamp_utc", "symbol", "exchange", "timeframe", "index_price"} else ""
        column_defs.append(f"{column_name} {data_type}{not_null}")
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                f"""
                CREATE TABLE {clause}{{}} (
                    {", ".join(column_defs)},
                    PRIMARY KEY (timestamp_utc)
                )
                """
            ).format(sql.Identifier(table_name))
        )
    connection.commit()


def _validate_database_local(connection: psycopg2.extensions.connection, table_name: str) -> dict:
    """Проверяет схему таблицы и очищает поврежденные данные."""
    schema = _read_table_schema(connection, table_name)
    schema_mismatch = bool(schema) and schema != _EXPECTED_TABLE_SCHEMA
    extra_columns = {column_name for column_name, _ in schema} - {column_name for column_name, _ in _EXPECTED_TABLE_SCHEMA}
    has_forbidden_columns = bool(extra_columns & _FORBIDDEN_TABLE_COLUMNS)
    table_recreated = False
    table_dropped = False

    if not schema:
        _create_market_table(connection, table_name)
        table_recreated = True
    elif schema_mismatch or extra_columns or has_forbidden_columns:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(table_name)))
        connection.commit()
        table_dropped = True
        _create_market_table(connection, table_name)
        table_recreated = True

    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("DELETE FROM {} WHERE index_price IS NULL OR timestamp_utc IS NULL").format(
                sql.Identifier(table_name)
            )
        )
        deleted_null_rows = max(cursor.rowcount, 0)
        cursor.execute(
            sql.SQL(
                """
                DELETE FROM {table_name} AS target
                USING (
                    SELECT ctid
                    FROM (
                        SELECT
                            ctid,
                            ROW_NUMBER() OVER (
                                PARTITION BY timestamp_utc, symbol, timeframe
                                ORDER BY ctid DESC
                            ) AS row_number
                        FROM {table_name}
                    ) ranked
                    WHERE row_number > 1
                ) duplicates
                WHERE target.ctid = duplicates.ctid
                """
            ).format(table_name=sql.Identifier(table_name))
        )
        deleted_duplicate_rows = max(cursor.rowcount, 0)
    connection.commit()

    final_schema = _read_table_schema(connection, table_name)
    return {
        "table_name": table_name,
        "table_dropped": table_dropped,
        "table_recreated": table_recreated,
        "deleted_null_rows": deleted_null_rows,
        "deleted_duplicate_rows": deleted_duplicate_rows,
        "schema": final_schema,
    }


def validate_database(config: dict, table_name: str) -> dict:
    """Проверяет и очищает таблицу PostgreSQL перед работой интерфейса."""
    conn = connect_db(config)
    try:
        backend_validator = getattr(builder, "validate_database", None)
        if callable(backend_validator):
            return backend_validator(conn, table_name)
        return _validate_database_local(conn, table_name)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_download_progress(
    progress_bar,
    status_placeholder,
    loaded_candles: int,
    total_candles: int,
    final: bool = True,
) -> None:
    """Обновляет прогресс скачивания по числу загруженных свечей."""
    if progress_bar is None or status_placeholder is None:
        return
    total_value = max(int(total_candles), 1)
    loaded_value = max(0, min(int(loaded_candles), total_value))
    progress = loaded_value / total_value
    progress = max(0.0, min(progress, 1.0))
    if not final:
        progress = min(progress, 0.99)
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
        refresh_start = max(
            builder.ceil_to_step(request_params["launch_time_ms"], request_params["step_ms"]),
            request_params["start_ms"] - rsi_period * request_params["step_ms"],
        )
        combined_rows = builder.fetch_db_rows(
            conn, request_params["table_name"], refresh_start, request_params["end_ms"]
        )
        if not missing_requested:
            persisted_rows = [combined_rows[timestamp] for timestamp in sorted(combined_rows)]
            if builder.has_persisted_rsi(persisted_rows, rsi_period):
                set_download_progress(
                    progress_bar,
                    status_placeholder,
                    total_candles,
                    total_candles,
                    final=True,
                )
                if progress_bar is not None and status_placeholder is not None:
                    status_placeholder.text("RSI loaded from PostgreSQL")
                return {"inserted": 0, "updated": 0, "downloaded_ranges": []}
            if progress_bar is not None and status_placeholder is not None:
                status_placeholder.text("Computing and saving missing RSI")

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
                    final=False,
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
            set_download_progress(progress_bar, status_placeholder, loaded_candles, total_candles, final=False)

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
            total_candles - 1,
            total_candles,
            final=False,
        )
        if progress_bar is not None and status_placeholder is not None:
            status_placeholder.text("Writing rows to PostgreSQL")
        _, inserted, updated = builder.rebuild_rsi_and_upsert_rows(
            conn,
            request_params["table_name"],
            rows_to_write,
            rsi_period,
        )
        set_download_progress(
            progress_bar,
            status_placeholder,
            total_candles,
            total_candles,
            final=True,
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

st.set_page_config(page_title="ModelLine — " + t("download.title"), layout="wide", initial_sidebar_state="collapsed")

_hcols = st.columns([8, 1])
with _hcols[1]:
    render_lang_toggle(key="dl_lang")

render_back_button()

st.title(t("download.title"))
st.caption(t("download.caption"))

# ---------------------------------------------------------------------------
# Конфигурация подключения
# ---------------------------------------------------------------------------
restored_config = load_db_config(load_local_config())

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
for _key, _default in (
    ("ds_coverage", None),
    ("ds_dataset", None),
    ("ds_last_download", None),
    ("ds_controls_sig", None),
    ("ds_validation_sig", None),
    ("ds_validation_report", None),
):
    if _key not in st.session_state:
        st.session_state[_key] = _default
if st.session_state.ds_dataset is None:
    st.session_state.ds_dataset = pd.DataFrame()

# ---------------------------------------------------------------------------
# Восстановление параметров из store (один раз за сессию)
# ---------------------------------------------------------------------------
def _iso_to_date(s: object) -> "date | None":
    if not s:
        return None
    try:
        return date.fromisoformat(str(s).split(" ")[0])
    except (ValueError, TypeError):
        return None

if "_ds_prefs_loaded" not in st.session_state:
    _p = load_ui_prefs()
    st.session_state.setdefault("ds_symbol",        _p.get("ds_symbol", "BTCUSDT"))
    st.session_state.setdefault("ds_timeframe",     _p.get("ds_timeframe", "60m"))
    st.session_state.setdefault("ds_date_from_iso", _p.get("ds_date_from") or "2024-01-01")
    st.session_state.setdefault("ds_date_to_iso",   _p.get("ds_date_to") or date.today().isoformat())
    st.session_state["_ds_prefs_loaded"] = True

def _date_val_to_iso(v: object) -> "str | None":
    return v.isoformat() if isinstance(v, date) else None

def _save_ds_prefs() -> None:
    _df = st.session_state.get("ds_date_from")
    _dt = st.session_state.get("ds_date_to")
    save_ui_prefs({
        "ds_symbol":    st.session_state.get("ds_symbol", "BTCUSDT"),
        "ds_timeframe": st.session_state.get("ds_timeframe", "60m"),
        "ds_date_from": _date_val_to_iso(_df) if isinstance(_df, date) else None,
        "ds_date_to":   _date_val_to_iso(_dt) if isinstance(_dt, date) else None,
    })

# ---------------------------------------------------------------------------
# Параметры инструмента
# ---------------------------------------------------------------------------
st.subheader(t("download.params"))
_tf_keys = list(builder.TIMEFRAMES.keys())
_tf_saved = st.session_state.get("ds_timeframe", "60m")
_tf_idx   = _tf_keys.index(_tf_saved) if _tf_saved in _tf_keys else _tf_keys.index("60m")

control_cols = st.columns(4)
symbol = control_cols[0].text_input(
    t("common.symbol"), key="ds_symbol", on_change=_save_ds_prefs,
)
timeframe = control_cols[1].selectbox(
    t("common.timeframe"), options=_tf_keys, index=_tf_idx,
    key="ds_timeframe", on_change=_save_ds_prefs,
)
start_date = control_cols[2].date_input(
    t("common.date_from"),
    value=_iso_to_date(st.session_state.get("ds_date_from_iso")) or date(2024, 1, 1),
    key="ds_date_from", on_change=_save_ds_prefs,
)
end_date = control_cols[3].date_input(
    t("common.date_to"),
    value=_iso_to_date(st.session_state.get("ds_date_to_iso")) or date.today(),
    key="ds_date_to", on_change=_save_ds_prefs,
)

# Always persist current widget values so a page refresh restores them
save_ui_prefs({
    "ds_symbol":    st.session_state.get("ds_symbol", "BTCUSDT"),
    "ds_timeframe": st.session_state.get("ds_timeframe", "60m"),
    "ds_date_from": start_date.isoformat(),
    "ds_date_to":   end_date.isoformat(),
})

# ---------------------------------------------------------------------------
# Подключение к БД (roadmap #4 — общий компонент)
# ---------------------------------------------------------------------------
from services.ui_components import render_db_settings  # noqa: E402
_ov = render_db_settings(restored_config, save_key="_ds_db_save", clear_key="_ds_db_clear")
db_config = load_db_config(_ov)
db_status = probe_db_connection(db_config)
render_db_status(db_config, db_status)

# ---------------------------------------------------------------------------
# Сброс кешированных результатов при смене параметров
# ---------------------------------------------------------------------------
_controls_sig = (
    symbol, timeframe,
    start_date.isoformat(), end_date.isoformat(),
    db_config["host"], str(db_config["port"]), db_config["database"],
)
if st.session_state.ds_controls_sig != _controls_sig:
    st.session_state.ds_controls_sig  = _controls_sig
    st.session_state.ds_coverage      = None
    st.session_state.ds_dataset       = pd.DataFrame()
    st.session_state.ds_last_download = None

st.divider()

# ---------------------------------------------------------------------------
# Кнопки действий
# ---------------------------------------------------------------------------
button_cols = st.columns(3)
check_clicked    = button_cols[0].button(t("download.btn_check"),    use_container_width=True, disabled=not db_status["connected"])
download_clicked = button_cols[1].button(t("download.btn_download"), use_container_width=True, disabled=not db_status["connected"])
load_clicked     = button_cols[2].button(t("download.btn_load"),     use_container_width=True, disabled=not db_status["connected"])

if start_date > end_date:
    st.error(t("download.date_error"))
else:
    try:
        request_params = make_request(symbol, timeframe, start_date, end_date)
    except Exception as exc:
        request_params = None
        st.error(str(exc))

    validation_error = None
    if request_params and db_status["connected"]:
        _val_sig = (
            db_config["host"], str(db_config["port"]),
            db_config["database"], db_config.get("user", ""),
            request_params["table_name"],
        )
        if st.session_state.ds_validation_sig != _val_sig:
            try:
                with st.spinner(f"{t('download.validating')} {request_params['table_name']}..."):
                    st.session_state.ds_validation_report = validate_database(db_config, request_params["table_name"])
                    st.session_state.ds_validation_sig = _val_sig
            except Exception as exc:
                st.session_state.ds_validation_sig    = None
                st.session_state.ds_validation_report = None
                validation_error = str(exc)
                st.error(f"{t('common.db_error')}: {validation_error}")

    if request_params and not db_status["connected"]:
        st.info(t("download.no_db"))

    if request_params and db_status["connected"] and validation_error is None and check_clicked:
        save_local_config(db_config)
        with st.spinner(t("download.checking")):
            st.session_state.ds_coverage = check_coverage(db_config, request_params)

    if request_params and db_status["connected"] and validation_error is None and download_clicked:
        save_local_config(db_config)
        # roadmap #7 — st.status вместо spinner для длинных операций
        with st.status(t("download.downloading"), expanded=True) as _dl_status:
            _pb  = st.progress(0)
            _sph = st.empty()
            try:
                st.session_state.ds_last_download = download_missing(
                    db_config, request_params,
                    progress_bar=_pb, status_placeholder=_sph,
                )
                st.session_state.ds_coverage = check_coverage(db_config, request_params)
                _dl_status.update(label=t("download.done"), state="complete", expanded=False)
            except Exception as exc:
                _dl_status.update(label=t("download.failed"), state="error")
                st.error(str(exc))

    if request_params and db_status["connected"] and validation_error is None and load_clicked:
        save_local_config(db_config)
        with st.spinner(t("download.loading_ds")):
            st.session_state.ds_dataset = load_dataset(db_config, request_params)

# ---------------------------------------------------------------------------
# Покрытие
# ---------------------------------------------------------------------------
if st.session_state.ds_coverage:
    cov = st.session_state.ds_coverage
    st.divider()
    st.subheader(t("download.coverage"))
    cov_cols = st.columns(5)
    cov_cols[0].metric(t("common.table"),          cov["table_name"])
    cov_cols[1].metric(t("common.status"),         cov["status"])
    cov_cols[2].metric(t("download.expected"),     cov["expected_count"])
    cov_cols[3].metric(t("download.existing"),     cov["existing_count"])
    cov_cols[4].metric(t("download.missing_count"), cov["missing_count"])
    iv_cols = st.columns(2)
    iv_cols[0].write(t("download.existing_ranges"))
    iv_cols[0].dataframe(ranges_frame(cov["existing_ranges"]), width="stretch", hide_index=True)
    iv_cols[1].write(t("download.missing_ranges"))
    iv_cols[1].dataframe(ranges_frame(cov["missing_ranges"]), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Результат последней загрузки
# ---------------------------------------------------------------------------
if st.session_state.ds_last_download is not None:
    dl = st.session_state.ds_last_download
    st.subheader(t("download.dl_result"))
    _rc = st.columns(2)
    _rc[0].metric(t("download.inserted"), dl["inserted"])
    _rc[1].metric(t("download.updated"),  dl["updated"])
    if dl["downloaded_ranges"]:
        st.dataframe(ranges_frame(dl["downloaded_ranges"]), width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Датасет, статистика, графики
# ---------------------------------------------------------------------------
if not st.session_state.ds_dataset.empty:
    dataset = st.session_state.ds_dataset
    summary, missing = dataset_summary(dataset)
    feat_cols = builder.get_feature_columns(dataset)

    st.divider()
    st.subheader(t("download.ds_summary"))
    _sc = st.columns(5)
    _sc[0].metric(t("common.rows"),       summary["row_count"])
    _sc[1].metric(t("download.features"), len(feat_cols))
    _sc[2].metric(t("download.columns"),  len(dataset.columns))
    _sc[3].metric(t("download.from"),     summary["min_timestamp"][:10])
    _sc[4].metric(t("download.to"),       summary["max_timestamp"][:10])

    _miss_nonzero = missing[missing["missing_values"] > 0]
    if not _miss_nonzero.empty:
        with st.expander(f"{t('download.missing_vals')} ({len(_miss_nonzero)})"):
            st.dataframe(_miss_nonzero, width="stretch", hide_index=True)

    st.divider()
    st.subheader(t("download.charts"))
    available_plot_fields = get_plot_fields(dataset)
    default_fields = [f for f in ["index_price", "rsi"] if f in available_plot_fields]
    ctrl_cols = st.columns([3, 1])
    selected_fields = ctrl_cols[0].multiselect(
        t("download.metrics_display"),
        options=available_plot_fields,
        default=default_fields,
    )
    overlay_mode = ctrl_cols[1].checkbox(t("download.overlay"), value=False)
    render_charts(dataset, selected_fields, overlay_mode)

    st.divider()
    st.subheader(t("download.raw_data"))

    # roadmap #12 — column selector для сырых данных
    _default_cols = [c for c in ["timestamp_utc", "open", "high", "low", "close",
                                   "volume", "rsi", "index_price"] if c in dataset.columns]
    _sel_cols = st.multiselect(
        t("download.columns_select"),
        options=list(dataset.columns),
        default=_default_cols,
        key="ds_raw_cols",
    )
    st.dataframe(dataset[_sel_cols] if _sel_cols else dataset, width="stretch", hide_index=True)
