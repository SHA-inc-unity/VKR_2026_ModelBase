"""Страница загрузки и просмотра датасета Bybit."""
from __future__ import annotations

import io
import sys
import threading
import time as _time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2 import sql
import streamlit as st

try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx
    from streamlit.runtime.scriptrunner import get_script_run_ctx as _get_st_ctx
    _HAS_ST_CTX = True
except ImportError:
    _HAS_ST_CTX = False

# Пути: корень воркспейса (для builder) и каталог frontend (для services)
_HERE = Path(__file__).resolve()
_WORKSPACE_ROOT = _HERE.parents[2]
_FRONTEND_ROOT = _HERE.parents[1]
for _p in (_WORKSPACE_ROOT, _FRONTEND_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backend import dataset as builder
from backend.dataset.constants import MAX_PARALLEL_API_WORKERS
from backend.dataset.timelog import perf_stage, tlog
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
from services import job_manager
from services.store import store
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
        # fetch_db_timestamps передаёт 1 колонку вместо 50+ —
        # для 100k строк это 50× меньше данных по сети.
        existing_ts_set = (
            builder.fetch_db_timestamps(
                conn,
                request_params["table_name"],
                request_params["start_ms"],
                request_params["end_ms"],
            )
            if exists
            else set()
        )
    finally:
        conn.close()
    existing_ts = sorted(existing_ts_set)
    missing_ts = builder.find_missing_timestamps(
        existing_ts_set,
        request_params["start_ms"],
        request_params["end_ms"],
        request_params["step_ms"],
    )
    result = {
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
    # Fire-and-forget: подгружаем страницы таблицы в shared_buffers PostgreSQL
    if exists:
        def _prewarm_bg() -> None:
            try:
                _pw_conn = connect_db(config)
                try:
                    builder.prewarm_table(_pw_conn, request_params["table_name"])
                finally:
                    _pw_conn.close()
            except Exception:
                pass
        threading.Thread(target=_prewarm_bg, daemon=True).start()
    return result


def download_missing(
    config: dict,
    request_params: dict,
    rsi_period: int = 14,
    progress_bar=None,
    status_placeholder=None,
) -> dict:
    """Докачивает свечи и обновляет прогресс во время реального скачивания."""
    _job_t0 = _time.perf_counter()
    _stage_timings: dict[str, float] = {}
    tlog.info(
        "download_missing | JOB START table=%s range=[%d,%d] step_ms=%d",
        request_params["table_name"],
        request_params["start_ms"], request_params["end_ms"],
        request_params["step_ms"],
    )
    conn = connect_db(config)
    try:
        total_candles = (
            (request_params["end_ms"] - request_params["start_ms"]) // request_params["step_ms"] + 1
        )
        with perf_stage("download_missing.ensure_table", table=request_params["table_name"]):
            builder.ensure_table(conn, request_params["table_name"])
        _stage_timings["ensure_table"] = _time.perf_counter() - _job_t0
        # SQL generate_series находит пропуски целиком на стороне PostgreSQL.
        # Для полностью загруженного датасета никаких строк по сети не передаётся.
        with perf_stage(
            "download_missing.find_gaps",
            table=request_params["table_name"],
            expected=total_candles,
        ) as _fg_ctx:
            missing_requested = builder.find_missing_timestamps_sql(
                conn,
                request_params["table_name"],
                request_params["start_ms"],
                request_params["end_ms"],
                request_params["step_ms"],
            )
            _fg_ctx["missing"] = len(missing_requested)
        refresh_start = max(
            builder.ceil_to_step(request_params["launch_time_ms"], request_params["step_ms"]),
            request_params["start_ms"] - rsi_period * request_params["step_ms"],
        )
        with perf_stage(
            "download_missing.fetch_warmup_rows",
            table=request_params["table_name"],
        ) as _wf_ctx:
            combined_rows = builder.fetch_db_rows_raw(
                conn, request_params["table_name"], refresh_start, request_params["end_ms"]
            )
            _wf_ctx["rows"] = len(combined_rows)
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

        # Захватываем Streamlit script-run context из главного потока один раз.
        _script_ctx = _get_st_ctx() if _HAS_ST_CTX else None

        _start_ms = request_params["start_ms"]
        _end_ms = request_params["end_ms"]

        # Считаем количество уже загруженных in-range строк один раз до цикла.
        _in_range_count = sum(1 for ts in combined_rows if _start_ms <= ts <= _end_ms)

        if len(missing_ranges) > 1:
            # Несколько диапазонов: параллельная загрузка
            if status_placeholder is not None:
                status_placeholder.text(
                    f"Fetching {len(missing_ranges)} missing ranges in parallel from Bybit..."
                )
            with ThreadPoolExecutor(max_workers=min(len(missing_ranges), MAX_PARALLEL_API_WORKERS)) as _range_ex:
                _range_futures = {
                    _range_ex.submit(
                        builder.fetch_range_rows,
                        "linear",
                        request_params["symbol"],
                        request_params["timeframe"],
                        request_params["bybit_interval"],
                        rs,
                        re,
                        request_params["funding_lookback_ms"],
                        request_params["open_interest_interval"],
                    ): (rs, re)
                    for rs, re in missing_ranges
                }
                for _f in as_completed(_range_futures):
                    _new_rows = _f.result()
                    combined_rows.update(_new_rows)
                    _in_range_count += sum(1 for ts in _new_rows if _start_ms <= ts <= _end_ms)
                    set_download_progress(
                        progress_bar, status_placeholder, _in_range_count, total_candles, final=False
                    )
        else:
            # Один диапазон: последовательная загрузка с детальным прогрессом
            for _range_idx, (range_start, range_end) in enumerate(missing_ranges):
                base_loaded_candles = _in_range_count  # O(1) — не сканируем combined_rows
                range_candles = (range_end - range_start) // request_params["step_ms"] + 1

                if status_placeholder is not None:
                    status_placeholder.text(
                        f"Fetching from Bybit: {range_candles} candles..."
                    )
                if progress_bar is not None:
                    _base_pct = base_loaded_candles / total_candles if total_candles > 0 else 0
                    progress_bar.progress(max(0.01, min(_base_pct, 0.97)))

                # _base захватывается по значению через default-аргумент, чтобы избежать
                # классической ошибки замыкания в цикле.
                def report_range_progress(
                    range_loaded_candles: int,
                    _base: int = base_loaded_candles,
                ) -> None:
                    """Обновляет прогресс из воркер-потока fetch_index_prices."""
                    if _HAS_ST_CTX and _script_ctx is not None:
                        add_script_run_ctx(threading.current_thread(), _script_ctx)
                    current_visible = _base + range_loaded_candles
                    if current_visible >= total_candles:
                        if progress_bar is not None:
                            progress_bar.progress(0.99)
                        if status_placeholder is not None:
                            status_placeholder.text("Fetching RSI warm-up data...")
                    else:
                        set_download_progress(
                            progress_bar,
                            status_placeholder,
                            current_visible,
                            total_candles,
                            final=False,
                        )

                new_rows = builder.fetch_range_rows(
                    "linear",
                    request_params["symbol"],
                    request_params["timeframe"],
                    request_params["bybit_interval"],
                    range_start,
                    range_end,
                    request_params["funding_lookback_ms"],
                    request_params["open_interest_interval"],
                    progress_callback=report_range_progress,
                    progress_start_ms=_start_ms,
                    progress_end_ms=_end_ms,
                )
                combined_rows.update(new_rows)

                # Инкрементальный подсчёт: сканируем только новые строки (O(n_range))
                _in_range_count += sum(1 for ts in new_rows if _start_ms <= ts <= _end_ms)
                loaded_candles = _in_range_count

                if loaded_candles >= total_candles:
                    if progress_bar is not None:
                        progress_bar.progress(0.99)
                    if status_placeholder is not None:
                        status_placeholder.text("Fetching RSI warm-up data...")
                else:
                    set_download_progress(progress_bar, status_placeholder, loaded_candles, total_candles, final=False)

        ordered_ts = list(
            range(refresh_start, request_params["end_ms"] + request_params["step_ms"], request_params["step_ms"])
        )
        still_missing = [ts for ts in ordered_ts if ts not in combined_rows]
        rows_to_write = [combined_rows[ts] for ts in ordered_ts if ts in combined_rows]
        if not rows_to_write:
            raise RuntimeError(
                "Bybit returned no data for the requested range — "
                f"{len(still_missing)} timestamps are unavailable starting "
                f"at {builder.ms_to_datetime(still_missing[0]).isoformat()}"
            )

        if progress_bar is not None:
            progress_bar.progress(0.99)
        if status_placeholder is not None:
            status_placeholder.text("Computing RSI and features...")

        def _on_upsert_batch(written: int, total: int) -> None:
            if status_placeholder is not None:
                status_placeholder.text(f"Writing to PostgreSQL: {written} / {total} rows")

        _, inserted, updated = None, 0, 0
        with perf_stage(
            "download_missing.rebuild_rsi_and_upsert",
            table=request_params["table_name"],
            rows=len(rows_to_write),
        ) as _ru_ctx:
            _, inserted, updated = builder.rebuild_rsi_and_upsert_rows(
                conn,
                request_params["table_name"],
                rows_to_write,
                rsi_period,
                on_upsert_batch=_on_upsert_batch,
                write_start_ms=request_params["start_ms"],
            )
            _ru_ctx["inserted"] = inserted
            _ru_ctx["updated"] = updated
        actual_user_candles = _in_range_count  # уже посчитано инкрементально
        set_download_progress(
            progress_bar,
            status_placeholder,
            actual_user_candles,
            actual_user_candles,
            final=True,
        )
        # Подгружаем таблицу в shared_buffers — быстрее для последующих read-запросов
        if status_placeholder is not None:
            status_placeholder.text("Prewarming table in PostgreSQL RAM...")
        with perf_stage("download_missing.prewarm", table=request_params["table_name"]):
            builder.prewarm_table(conn, request_params["table_name"])
        tlog.info(
            "download_missing | JOB DONE table=%s inserted=%d updated=%d ranges=%d total_elapsed=%.3fs",
            request_params["table_name"], inserted, updated,
            len(missing_ranges), _time.perf_counter() - _job_t0,
        )
        return {
            "inserted": inserted,
            "updated": updated,
            "downloaded_ranges": missing_ranges,
            "skipped_timestamps": len(still_missing),
        }
    except Exception:
        tlog.exception(
            "download_missing | JOB FAILED table=%s total_elapsed=%.3fs",
            request_params["table_name"], _time.perf_counter() - _job_t0,
        )
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


def _df_to_csv_bytes_chunked(
    df: pd.DataFrame,
    chunk_size: int = 50_000,
    on_progress=None,
) -> bytes:
    """Thin wrapper around ``backend.csv_io.stream_csv_bytes`` для обратной совместимости.

    Все новые вызовы должны импортировать ``stream_csv_bytes`` напрямую.
    """
    from backend.csv_io import stream_csv_bytes
    return stream_csv_bytes(df, chunk_size=chunk_size, on_progress=on_progress)


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
# История запросов: Redis/SQLite-хранилище
# ---------------------------------------------------------------------------
_HISTORY_KEY    = "download:history"
_COVERAGE_KEY   = "download:coverage"
_ACTIVE_JOB_KEY = "download:active_job"


def _load_history_from_store() -> list:
    """Читает историю из store (Redis/SQLite)."""
    raw = store.get_json(_HISTORY_KEY) or []
    return raw if isinstance(raw, list) else []


def _save_history_to_store(history: list) -> None:
    store.set_json(_HISTORY_KEY, history[:100])


def _save_coverage_to_store(cov: dict | None, controls_sig: tuple | None = None) -> None:
    if cov is not None:
        store.set_json(_COVERAGE_KEY, {"cov": cov, "sig": list(controls_sig) if controls_sig else None})
    else:
        store.delete(_COVERAGE_KEY)


def _update_history_entry(job_id: str, result: str, duration: str) -> None:
    """Обновляет запись по job_id в session state и store."""
    for collection in (
        st.session_state.get("ds_query_history", []),
        _load_history_from_store(),
    ):
        for entry in collection:
            if entry.get("job_id") == job_id:
                entry["result"] = result
                entry["duration"] = duration
                entry["job_id"] = None
    st.session_state.ds_query_history = st.session_state.get("ds_query_history", [])
    _save_history_to_store(st.session_state.ds_query_history)


# ---------------------------------------------------------------------------
# ALL-timeframes background download
# ---------------------------------------------------------------------------
def _download_all_timeframes(
    config: dict,
    symbol: str,
    timeframe_list: list,
    start_date: "date",
    end_date: "date",
    rsi_period: int = 14,
    progress_bar=None,
    status_placeholder=None,
) -> dict:
    """Скачивает данные по всем таймфреймам параллельно (до 4 одновременно).

    Каждый таймфрейм использует своё DB-соединение и свой пул Bybit-воркеров.
    4 параллельных ТФ × 10 внутренних воркеров = ≤40 одновременных API-запросов,
    что укладывается в лимит Bybit 120 req/s.
    """
    total          = len(timeframe_list)
    total_inserted = 0
    total_updated  = 0
    per_tf: dict   = {}
    _done_count    = 0
    _active: set   = set()
    _lock          = threading.Lock()

    def _download_one(tf: str) -> tuple[str, dict]:
        with _lock:
            _active.add(tf)
        try:
            rp = make_request(symbol, tf, start_date, end_date)
            validate_database(config, rp["table_name"])
            result = download_missing(
                config, rp,
                rsi_period=rsi_period,
                progress_bar=None,
                status_placeholder=None,
            )
            return tf, result
        except Exception as exc:
            return tf, {"error": str(exc)}
        finally:
            with _lock:
                _active.discard(tf)

    _max_workers = min(total, 8)
    with ThreadPoolExecutor(max_workers=_max_workers) as _ex:
        _futures = {_ex.submit(_download_one, tf): tf for tf in timeframe_list}
        for _fut in as_completed(_futures):
            _tf_done, _result = _fut.result()
            per_tf[_tf_done]   = _result
            total_inserted    += _result.get("inserted", 0)
            total_updated     += _result.get("updated", 0)
            _done_count       += 1
            if progress_bar is not None:
                progress_bar.progress(_done_count / total)
            if status_placeholder is not None:
                with _lock:
                    _running_now = sorted(_active)
                _run_str = ", ".join(_running_now) if _running_now else "завершение..."
                _err_flag = " ✗" if "error" in _result else " ✓"
                status_placeholder.text(
                    f"{_tf_done}{_err_flag} [{_done_count}/{total}] | "
                    f"активны: {_run_str}"
                )

    return {
        "inserted": total_inserted,
        "updated":  total_updated,
        "downloaded_ranges": [],
        "per_timeframe": per_tf,
    }


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
for _key, _default in (
    ("ds_coverage", None),
    ("ds_all_coverages", None),
    ("ds_dataset", None),
    ("ds_last_download", None),
    ("ds_controls_sig", None),
    ("ds_validation_sig", None),
    ("ds_validation_report", None),
    ("ds_active_job_id", None),
    ("ds_active_job_params", None),
    ("ds_job_error", None),
    ("ds_csv_bytes", None),
    ("ds_csv_export_pending", False),
    ("ds_all_csv_pending",    False),
):
    if _key not in st.session_state:
        st.session_state[_key] = _default

# История: один раз за сессию загружаем из store и чиним «осиротевшие» записи
if "ds_query_history" not in st.session_state:
    _stored = _load_history_from_store()
    # Если процесс был перезапущен, задачи с job_id уже не существуют → «прервано»
    # Если процесс жив и задача ещё выполняется → восстанавливаем ds_active_job_id
    for _e in _stored:
        if _e.get("job_id"):
            _j = job_manager.get(_e["job_id"])
            if _j and _j["status"] == "running":
                # Задача ещё жива — восстанавливаем активное задание
                if not st.session_state.get("ds_active_job_id"):
                    st.session_state.ds_active_job_id = _e["job_id"]
                    _saved_active = store.get_json(_ACTIVE_JOB_KEY) or {}
                    if _saved_active.get("job_id") == _e["job_id"]:
                        st.session_state.ds_active_job_params = _saved_active.get("request_params")
            elif _j and _j["status"] == "done":
                # Задача завершилась пока страница была закрыта — читаем реальный результат
                _ji_result = _j.get("result") or {}
                _ji_ins  = _ji_result.get("inserted", 0)
                _ji_upd  = _ji_result.get("updated", 0)
                _ji_dur  = _time.monotonic() - _j["started_at"]
                _e["result"]   = f"вставлено {_ji_ins}, обновлено {_ji_upd} строк"
                _e["duration"] = f"{_ji_dur:.2f}s"
                _e["job_id"]   = None
                store.delete(_ACTIVE_JOB_KEY)
            elif _j and _j["status"] == "error":
                # Задача завершилась с ошибкой
                _e["result"]   = f"{t('download.qh_error')}: {_j.get('error', '?')}"
                _e["duration"] = t("download.qh_error")
                _e["job_id"]   = None
                store.delete(_ACTIVE_JOB_KEY)
            else:
                # job_manager пуст (процесс перезапустился) → «прервано»
                _e["duration"] = t("download.qh_interrupted")
                _e["result"]   = t("download.qh_interrupted")
                _e["job_id"]   = None
                store.delete(_ACTIVE_JOB_KEY)
    st.session_state.ds_query_history = _stored
    _save_history_to_store(_stored)

# Покрытие: восстановить из store один раз за сессию (вместе с controls_sig)
if st.session_state.ds_coverage is None:
    _stored_cov_blob = store.get_json(_COVERAGE_KEY)
    if _stored_cov_blob and isinstance(_stored_cov_blob, dict) and "cov" in _stored_cov_blob:
        st.session_state.ds_coverage = _stored_cov_blob["cov"]
        if _stored_cov_blob.get("sig"):
            st.session_state.ds_controls_sig = tuple(_stored_cov_blob["sig"])

if st.session_state.ds_dataset is None:
    st.session_state.ds_dataset = pd.DataFrame()


def _record_query(
    action_key: str,
    rp: dict,
    result_str: str,
    duration_s: float | str,
    sql_hint: str,
    *,
    job_id: str | None = None,
) -> None:
    """Добавляет запись в историю (session state + store). Новые — вверху, не более 100."""
    entry = {
        "id": job_id or uuid.uuid4().hex,
        "time": datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC"),
        "action": t(action_key),
        "params": (
            f"{rp.get('symbol', '?')} {rp.get('timeframe', '?')}  "
            f"{rp.get('start_ms', 0) and builder.ms_to_datetime(rp['start_ms']).strftime('%Y-%m-%d')}"
            f" → {rp.get('end_ms', 0) and builder.ms_to_datetime(rp['end_ms']).strftime('%Y-%m-%d')}"
        ),
        "result": result_str,
        "duration": f"{duration_s:.2f}s" if isinstance(duration_s, float) else str(duration_s),
        "sql": sql_hint,
        "job_id": job_id,
    }
    history: list = st.session_state.get("ds_query_history", [])
    history.insert(0, entry)
    st.session_state.ds_query_history = history[:100]
    _save_history_to_store(history[:100])


class _BgProgressBar:
    """Имитирует st.progress() для фонового потока."""
    def __init__(self, job_id: str) -> None:
        self._job_id = job_id

    def progress(self, value: float) -> None:
        job_manager.update(self._job_id, progress=float(value))


class _BgStatusText:
    """Имитирует st.empty().text() для фонового потока."""
    def __init__(self, job_id: str) -> None:
        self._job_id = job_id

    def text(self, msg: str) -> None:
        job_manager.update(self._job_id, status_text=str(msg))


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
_KNOWN_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "UNIUSDT", "ATOMUSDT", "LTCUSDT", "NEARUSDT",
    "TONUSDT", "SUIUSDT", "APTUSDT",
]
_TF_ALL = "ALL"

st.subheader(t("download.params"))
_sym_saved    = st.session_state.get("ds_symbol", "BTCUSDT")
_sym_options  = _KNOWN_SYMBOLS if _sym_saved in _KNOWN_SYMBOLS else [_sym_saved] + _KNOWN_SYMBOLS
_sym_idx      = _sym_options.index(_sym_saved)

_tf_keys_base = list(builder.TIMEFRAMES.keys())
_tf_keys      = [_TF_ALL] + _tf_keys_base
_tf_saved     = st.session_state.get("ds_timeframe", "60m")
_tf_idx       = _tf_keys.index(_tf_saved) if _tf_saved in _tf_keys else _tf_keys.index("60m")

control_cols = st.columns(4)
symbol = control_cols[0].selectbox(
    t("common.symbol"), options=_sym_options, index=_sym_idx,
    key="ds_symbol", on_change=_save_ds_prefs,
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
    st.session_state.ds_controls_sig       = _controls_sig
    st.session_state.ds_coverage           = None
    st.session_state.ds_all_coverages      = None
    st.session_state.ds_dataset            = pd.DataFrame()
    st.session_state.ds_last_download      = None
    st.session_state.ds_csv_bytes          = None
    st.session_state.ds_csv_export_pending = False
    st.session_state.ds_all_csv_pending    = False

st.divider()

# ---------------------------------------------------------------------------
# Кнопки действий
# ---------------------------------------------------------------------------
# Кнопка "Загрузить датасет" (btn_load) удалена — см. docs/PERFORMANCE_AUDIT_REPORT.md.
# Полная материализация 2M+ строк × 50 колонок в st.session_state потребляла 1.5–2 ГБ RAM
# и блокировала Streamlit-rerun на 3–8 секунд. Экспорт CSV по-прежнему работает напрямую
# из БД через stream_csv_bytes (чанковый, ~50–100 МБ peak RAM).
button_cols = st.columns(3)
_active_job_id = st.session_state.get("ds_active_job_id")
_job_running = bool(
    _active_job_id
    and job_manager.get(_active_job_id) is not None
    and job_manager.get(_active_job_id)["status"] == "running"
)
_ds_ready = (
    st.session_state.ds_dataset is not None
    and not st.session_state.ds_dataset.empty
)
check_clicked    = button_cols[0].button(t("download.btn_check"),      use_container_width=True, disabled=not db_status["connected"])
download_clicked = button_cols[1].button(t("download.btn_download"),   use_container_width=True, disabled=not db_status["connected"] or _job_running)
_is_all_mode = (timeframe == _TF_ALL)
# Кнопка экспорта CSV: активна при подключении к БД.
# В режиме ALL → скачивается ZIP-архив с отдельным CSV на каждый таймфрейм.
# ВАЖНО: to_csv() НЕ вызывается inline — это выполнялось бы на каждый rerun Streamlit
# и замораживало UI + съедало сотни MB RAM. Используется ленивая генерация через pending-флаг.
_can_export = db_status["connected"]
if st.session_state.ds_csv_bytes is not None:
    # CSV / ZIP уже сгенерирован — показываем кнопку скачивания
    _csv_fname = (
        f"{symbol}_ALL_{start_date.isoformat()}_{end_date.isoformat()}.zip"
        if _is_all_mode
        else f"{symbol}_{timeframe}_dataset.csv"
    )
    _csv_mime = "application/zip" if _is_all_mode else "text/csv"
    button_cols[2].download_button(
        label=t("download.btn_export_csv"),
        data=st.session_state.ds_csv_bytes,
        file_name=_csv_fname,
        mime=_csv_mime,
        use_container_width=True,
    )
else:
    # Dataset ещё не загружен или CSV не сгенерирован → кнопка-триггер
    if button_cols[2].button(t("download.btn_export_csv"), use_container_width=True, disabled=not _can_export):
        if _is_all_mode:
            st.session_state.ds_all_csv_pending = True
        else:
            st.session_state.ds_csv_export_pending = True

if start_date > end_date:
    st.error(t("download.date_error"))
else:
    if _is_all_mode:
        request_params = None
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

    if not db_status["connected"]:
        st.info(t("download.no_db"))

    # ── Одиночный таймфрейм ─────────────────────────────────────────────────
    if request_params and db_status["connected"] and validation_error is None and check_clicked:
        save_local_config(db_config)
        with st.spinner(t("download.checking")):
            _t0 = datetime.now(tz=timezone.utc)
            st.session_state.ds_coverage = check_coverage(db_config, request_params)
            _save_coverage_to_store(st.session_state.ds_coverage, _controls_sig)
            _dur = (datetime.now(tz=timezone.utc) - _t0).total_seconds()
        _cov = st.session_state.ds_coverage
        _cov_result = (
            f"{_cov['existing_count']} / {_cov['expected_count']} строк — {_cov['status']}, "
            f"пропусков: {_cov['missing_count']}"
        ) if _cov else "ошибка"
        _record_query(
            "download.qh_act_check", request_params, _cov_result, _dur,
            "fetch_db_timestamps → find_missing_timestamps",
        )

    if request_params and db_status["connected"] and validation_error is None and download_clicked:
        save_local_config(db_config)
        _jid = uuid.uuid4().hex
        st.session_state.ds_active_job_id     = _jid
        st.session_state.ds_active_job_params = request_params
        st.session_state.ds_job_error         = None
        store.set_json(_ACTIVE_JOB_KEY, {"job_id": _jid, "request_params": request_params})
        _record_query(
            "download.qh_act_download", request_params,
            t("download.qh_in_progress"),
            t("download.qh_in_progress"),
            "find_missing_timestamps_sql → fetch_range_rows → upsert_rows (COPY)",
            job_id=_jid,
        )
        job_manager.submit(
            _jid, download_missing,
            db_config, request_params,
            progress_bar=_BgProgressBar(_jid),
            status_placeholder=_BgStatusText(_jid),
        )
        st.rerun()

    # ── CSV export on-demand (одиночный ТФ) ─────────────────────────────────
    if (
        st.session_state.get("ds_csv_export_pending")
        and request_params is not None
        and db_status["connected"]
    ):
        st.session_state.ds_csv_export_pending = False
        _csv_prog = st.progress(0.0, text="Экспорт CSV: читаем данные...")
        _csv_status = st.empty()
        try:
            # Если датасет уже загружен в session_state — используем его (без лишнего запроса в БД)
            _csv_export_df = (
                st.session_state.ds_dataset
                if _ds_ready
                else load_dataset(db_config, request_params)
            )
            if not _csv_export_df.empty:
                _csv_n = len(_csv_export_df)
                _csv_status.text(f"Экспорт CSV: сериализуем {_csv_n:,} строк по чанкам...")

                def _csv_on_progress(done: int, total: int) -> None:
                    _csv_prog.progress(done / total, text=f"Экспорт CSV: {done:,} / {total:,} строк")

                st.session_state.ds_csv_bytes = _df_to_csv_bytes_chunked(
                    _csv_export_df,
                    on_progress=_csv_on_progress,
                )
        except Exception as _csv_exc:
            st.error(f"Ошибка при экспорте CSV: {_csv_exc}")
        finally:
            _csv_prog.empty()
            _csv_status.empty()
        st.rerun()

    # ── CSV export ALL → ZIP ──────────────────────────────────────────────────
    if st.session_state.get("ds_all_csv_pending") and _is_all_mode and db_status["connected"]:
        st.session_state.ds_all_csv_pending = False
        _tf_list_zip = list(builder.TIMEFRAMES.keys())
        _n_tfs = len(_tf_list_zip)
        _zip_prog   = st.progress(0.0, text="ZIP: начинаем...")
        _zip_status = st.empty()
        try:
            _zip_buf = io.BytesIO()
            with zipfile.ZipFile(_zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as _zf:
                # Обрабатываем по одному таймфрейму: пиковый RAM = 1 DF × 50 col, а не 11 сразу
                for _i_tf, _tf_z in enumerate(_tf_list_zip):
                    _zip_prog.progress(_i_tf / _n_tfs, text=f"ZIP [{_i_tf + 1}/{_n_tfs}]: загружаем {_tf_z}...")
                    _zip_status.text(f"Загружаем {symbol} {_tf_z}...")
                    try:
                        _rp_z  = make_request(symbol, _tf_z, start_date, end_date)
                        _df_z  = load_dataset(db_config, _rp_z)
                        if not _df_z.empty:
                            _fname_z = f"{symbol}_{_tf_z}_{start_date.isoformat()}_{end_date.isoformat()}.csv"
                            _zip_status.text(f"Сериализуем {_tf_z} ({len(_df_z):,} строк)...")
                            _csv_b = _df_to_csv_bytes_chunked(_df_z)
                            _zf.writestr(_fname_z, _csv_b)
                            del _df_z, _csv_b   # освобождаем RAM сразу после записи в ZIP
                    except Exception:
                        pass
            _zip_prog.progress(1.0, text=f"ZIP готов: {_n_tfs} таймфреймов")
            st.session_state.ds_csv_bytes = _zip_buf.getvalue()
        finally:
            _zip_prog.empty()
            _zip_status.empty()
        st.rerun()

    # Блок `load_clicked` удалён: кнопка "Загрузить датасет" больше не показывается.
    # Экспорт CSV читает данные из БД напрямую через load_dataset() → stream_csv_bytes,
    # без промежуточной материализации в st.session_state.ds_dataset.

    # ── Режим ALL: все таймфреймы ────────────────────────────────────────────
    if _is_all_mode and db_status["connected"] and check_clicked:
        save_local_config(db_config)
        _all_cov: dict = {}
        _t0_all_chk = datetime.now(tz=timezone.utc)
        with st.spinner("Проверяем все таймфреймы..."):
            _tf_list_all = list(builder.TIMEFRAMES.keys())

            def _check_one_tf(_tf_i: str) -> tuple[str, dict]:
                try:
                    _rp_i = make_request(symbol, _tf_i, start_date, end_date)
                    return _tf_i, check_coverage(db_config, _rp_i)
                except Exception as _exc_i:
                    return _tf_i, {"error": str(_exc_i)}

            with ThreadPoolExecutor(max_workers=len(_tf_list_all)) as _chk_ex:
                for _tf_key, _cov_val in _chk_ex.map(_check_one_tf, _tf_list_all):
                    _all_cov[_tf_key] = _cov_val
        st.session_state.ds_all_coverages = _all_cov
        _dur_all_chk = (datetime.now(tz=timezone.utc) - _t0_all_chk).total_seconds()
        _all_chk_ok  = sum(1 for _c in _all_cov.values() if "error" not in _c)
        _all_chk_err = sum(1 for _c in _all_cov.values() if "error"     in _c)
        _all_chk_rp  = {
            "symbol":    symbol,
            "timeframe": "ALL",
            "start_ms":  int(datetime.combine(start_date, time.min, tzinfo=timezone.utc).timestamp() * 1000),
            "end_ms":    int(datetime.combine(end_date,   time.min, tzinfo=timezone.utc).timestamp() * 1000),
        }
        _record_query(
            "download.qh_act_check", _all_chk_rp,
            f"ALL: {_all_chk_ok} таймфреймов проверено" + (f", {_all_chk_err} ошибок" if _all_chk_err else ""),
            _dur_all_chk,
            f"fetch_db_timestamps × {len(_tf_list_all)} таймфреймов → find_missing_timestamps",
        )

    if _is_all_mode and db_status["connected"] and download_clicked:
        save_local_config(db_config)
        _jid = uuid.uuid4().hex
        st.session_state.ds_active_job_id     = _jid
        st.session_state.ds_active_job_params = None
        st.session_state.ds_job_error         = None
        store.set_json(_ACTIVE_JOB_KEY, {"job_id": _jid, "request_params": None})
        _all_tf_list = list(builder.TIMEFRAMES.keys())
        _all_rp_stub = {
            "symbol":    symbol,
            "timeframe": "ALL",
            "start_ms":  int(datetime.combine(start_date, time.min, tzinfo=timezone.utc).timestamp() * 1000),
            "end_ms":    int(datetime.combine(end_date,   time.min, tzinfo=timezone.utc).timestamp() * 1000),
        }
        _record_query(
            "download.qh_act_download", _all_rp_stub,
            t("download.qh_in_progress"),
            t("download.qh_in_progress"),
            f"ALL ({', '.join(_all_tf_list)}): find_missing → fetch → upsert",
            job_id=_jid,
        )
        job_manager.submit(
            _jid, _download_all_timeframes,
            db_config, symbol, _all_tf_list,
            start_date, end_date,
            progress_bar=_BgProgressBar(_jid),
            status_placeholder=_BgStatusText(_jid),
        )
        st.rerun()

# ---------------------------------------------------------------------------
# Фоновый download: завершение или прогресс
# ---------------------------------------------------------------------------
_fin_jid = st.session_state.get("ds_active_job_id")
if _fin_jid:
    _fin_job = job_manager.get(_fin_jid)
    if _fin_job is None:
        # Процесс был перезапущен — job_manager пустой
        _update_history_entry(_fin_jid, t("download.qh_interrupted"), t("download.qh_interrupted"))
        st.session_state.ds_active_job_id     = None
        st.session_state.ds_active_job_params = None
        store.delete(_ACTIVE_JOB_KEY)
    elif _fin_job["status"] == "done":
        _fin_result = _fin_job.get("result") or {}
        _fin_inserted = _fin_result.get("inserted", 0)
        _fin_updated  = _fin_result.get("updated", 0)
        _fin_duration = _time.monotonic() - _fin_job["started_at"]
        _fin_result_str = f"вставлено {_fin_inserted}, обновлено {_fin_updated} строк"
        if _fin_result.get("skipped_timestamps", 0) > 0:
            st.warning(
                f"Bybit не вернул данные для {_fin_result['skipped_timestamps']} временных меток "
                "в начале диапазона — они пропущены."
            )
        _update_history_entry(_fin_jid, _fin_result_str, f"{_fin_duration:.2f}s")
        st.session_state.ds_last_download     = _fin_result
        _fin_rp = st.session_state.get("ds_active_job_params")
        if _fin_rp and db_status["connected"]:
            st.session_state.ds_coverage = check_coverage(db_config, _fin_rp)
            _fin_sig = (
                _fin_rp.get("symbol", ""), _fin_rp.get("timeframe", ""),
                builder.ms_to_datetime(_fin_rp["start_ms"]).strftime("%Y-%m-%d"),
                builder.ms_to_datetime(_fin_rp["end_ms"]).strftime("%Y-%m-%d"),
                db_config["host"], str(db_config["port"]), db_config["database"],
            )
            st.session_state.ds_controls_sig = _fin_sig
            _save_coverage_to_store(st.session_state.ds_coverage, _fin_sig)
        st.session_state.ds_active_job_id     = None
        st.session_state.ds_active_job_params = None
        store.delete(_ACTIVE_JOB_KEY)
        job_manager.cleanup_old()
        st.rerun()
    elif _fin_job["status"] == "error":
        _fin_err = _fin_job.get("error", "неизвестная ошибка")
        _update_history_entry(
            _fin_jid,
            f"{t('download.qh_error')}: {_fin_err}",
            t("download.qh_error"),
        )
        st.session_state.ds_active_job_id     = None
        st.session_state.ds_active_job_params = None
        store.delete(_ACTIVE_JOB_KEY)
        st.error(f"{t('download.failed')}: {_fin_err}")
        st.rerun()
    else:
        # status == "running" — показываем прогресс и поллим каждые 1.5 с
        _prog_val = float(_fin_job.get("progress", 0.0))
        _prog_txt = str(_fin_job.get("status_text", "..."))
        _fin_rp_run = st.session_state.get("ds_active_job_params")
        _is_all_run = (_fin_rp_run is None or (_fin_rp_run or {}).get("timeframe") == "ALL")
        with st.container(border=True):
            st.info(t("download.dl_running"))
            st.progress(_prog_val, text=_prog_txt if _prog_txt and _prog_txt != "..." else "...")
        _time.sleep(1.5)
        st.rerun()

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

if st.session_state.get("ds_all_coverages"):
    st.divider()
    st.subheader("Покрытие — все таймфреймы")
    _all_cov_rows = []
    for _tf_key, _c in st.session_state.ds_all_coverages.items():
        if "error" in _c:
            _all_cov_rows.append({
                "таймфрейм": _tf_key, "статус": "ошибка",
                "ожидается": "—", "есть": "—", "пропусков": "—",
                "примечание": _c["error"][:100],
            })
        else:
            _all_cov_rows.append({
                "таймфрейм": _tf_key,
                "статус":    _c["status"],
                "ожидается": _c["expected_count"],
                "есть":      _c["existing_count"],
                "пропусков": _c["missing_count"],
                "примечание": "",
            })
    st.dataframe(pd.DataFrame(_all_cov_rows), hide_index=True, use_container_width=True)

# ---------------------------------------------------------------------------
# Результат последней загрузки
# ---------------------------------------------------------------------------
if st.session_state.ds_last_download is not None:
    dl = st.session_state.ds_last_download
    st.subheader(t("download.dl_result"))
    _rc = st.columns(2)
    _rc[0].metric(t("download.inserted"), dl["inserted"])
    _rc[1].metric(t("download.updated"),  dl["updated"])
    if dl.get("skipped_timestamps", 0) > 0:
        st.warning(
            f"⚠️ {dl['skipped_timestamps']} временных меток пропущено — "
            f"Bybit не вернул эти данные (ранние исторические даты)."
        )
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
    _display_df = dataset[_sel_cols] if _sel_cols else dataset
    st.dataframe(_display_df, width="stretch", hide_index=True)

    _csv_filename = (
        f"{request_params['symbol'].lower()}"
        f"_{request_params['timeframe']}"
        f"_{start_date.isoformat()}_{end_date.isoformat()}.csv"
        if request_params
        else "dataset.csv"
    )
    st.download_button(
        label=t("download.export_csv"),
        data=_display_df.to_csv(index=False).encode("utf-8"),
        file_name=_csv_filename,
        mime="text/csv",
        help=t("download.export_csv_help"),
    )

# ---------------------------------------------------------------------------
# История запросов к базе данных
# ---------------------------------------------------------------------------
st.divider()
_qh_cols = st.columns([6, 2])
_qh_cols[0].subheader(t("download.qh_title"))
if _qh_cols[1].button(t("download.qh_clear"), key="ds_qh_clear", use_container_width=True):
    st.session_state.ds_query_history = []
    _save_history_to_store([])

_history: list = st.session_state.get("ds_query_history", [])

if not _history:
    st.caption(t("download.qh_empty"))
else:
    _hist_df = pd.DataFrame(_history, columns=["time", "action", "params", "result", "duration", "sql"])
    # «id» и «job_id» — служебные поля, не отображаем
    _hist_df.columns = [
        t("download.qh_time"),
        t("download.qh_action"),
        t("download.qh_params"),
        t("download.qh_result"),
        t("download.qh_duration"),
        t("download.qh_sql"),
    ]
    st.dataframe(
        _hist_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            t("download.qh_time"):     st.column_config.TextColumn(width="small"),
            t("download.qh_action"):   st.column_config.TextColumn(width="medium"),
            t("download.qh_params"):   st.column_config.TextColumn(width="medium"),
            t("download.qh_result"):   st.column_config.TextColumn(width="large"),
            t("download.qh_duration"): st.column_config.TextColumn(width="small"),
            t("download.qh_sql"):      st.column_config.TextColumn(width="large"),
        },
    )

# ---------------------------------------------------------------------------
# Поллинг: авто-перезапуск страницы пока фоновый download работает
# ---------------------------------------------------------------------------
_poll_jid = st.session_state.get("ds_active_job_id")
if _poll_jid:
    _poll_job = job_manager.get(_poll_jid)
    if _poll_job and _poll_job["status"] == "running":
        # Не используем sleep — это вызывает двойной рендер истории в Streamlit.
        # Вместо этого используем st.rerun() напрямую; браузер получит один ответ.
        st.rerun()
