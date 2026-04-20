"""FastAPI-приложение ModelLine REST API.

Эндпоинты:
    GET  /health                        — проверка работоспособности
    GET  /registry                      — список версий моделей
    DELETE /registry/{version_id}       — удалить запись из реестра
    GET  /predictions/{prefix}          — предсказания последней сессии
    GET  /metrics/{prefix}              — метрики из реестра (последняя версия)
    POST /retrain                       — запустить переобучение в фоне
    GET  /scheduler/status              — статус APScheduler
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.model.config import MODELS_DIR
from backend.model.report import (
    delete_registry_version,
    load_registry,
)

from .schemas import (
    HealthResponse,
    MetricsSummaryResponse,
    PredictionPoint,
    PredictionsResponse,
    RegistryEntry,
    RegistryResponse,
    RetrainRequest,
    RetrainResponse,
    SchedulerJobInfo,
    SchedulerStatusResponse,
)

_LOG = logging.getLogger(__name__)

# Глобальный планировщик (инициализируется при старте если нужно)
_scheduler = None


# ---------------------------------------------------------------------------
# Lifecycle (lifespan вместо устаревшего on_event)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    global _scheduler
    auto_start = os.getenv("SCHEDULER_AUTOSTART", "false").lower() == "true"
    if auto_start:
        try:
            from backend.scheduler import Scheduler
            _scheduler = Scheduler.from_env()
            _scheduler.start()
            _LOG.info("[api] Scheduler autostarted with %d jobs", len(_scheduler.list_jobs()))
        except Exception as exc:
            _LOG.warning("[api] Scheduler не запущен: %s", exc)
    yield
    if _scheduler is not None:
        _scheduler.stop()


app = FastAPI(
    title="ModelLine API",
    description="REST API для управления ML-моделями прогнозирования доходности.",
    version="1.0.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@app.get("/registry", response_model=RegistryResponse, tags=["registry"])
async def list_registry(
    prefix: str | None = Query(None, description="Фильтр по prefix"),
    limit: int = Query(50, ge=1, le=500),
) -> RegistryResponse:
    entries_raw = load_registry(
        models_dir=MODELS_DIR,
        prefix_filter=prefix or None,
        limit=limit,
    )
    entries = [RegistryEntry(**e) for e in entries_raw]
    return RegistryResponse(entries=entries, total=len(entries))


@app.delete("/registry/{version_id}", tags=["registry"])
async def remove_registry_entry(version_id: str) -> dict:
    ok = delete_registry_version(version_id, models_dir=MODELS_DIR)
    if not ok:
        raise HTTPException(status_code=404, detail=f"version_id '{version_id}' не найден")
    return {"deleted": version_id}


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------

@app.get("/predictions/{prefix}", response_model=PredictionsResponse, tags=["predictions"])
async def get_predictions(prefix: str) -> PredictionsResponse:
    pred_path = MODELS_DIR / f"{prefix}_predictions.json"
    if not pred_path.exists():
        raise HTTPException(status_code=404, detail=f"Предсказания для '{prefix}' не найдены")
    try:
        payload = json.loads(pred_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    predictions = [PredictionPoint(**p) for p in payload.get("predictions", [])]
    return PredictionsResponse(
        prefix=payload.get("prefix", prefix),
        saved_at=payload.get("saved_at", ""),
        n_samples=payload.get("n_samples", len(predictions)),
        metrics=payload.get("metrics"),
        best_params=payload.get("best_params"),
        predictions=predictions,
    )


# ---------------------------------------------------------------------------
# Metrics summary
# ---------------------------------------------------------------------------

@app.get("/metrics/{prefix}", response_model=MetricsSummaryResponse, tags=["metrics"])
async def get_metrics(prefix: str) -> MetricsSummaryResponse:
    entries = load_registry(models_dir=MODELS_DIR, prefix_filter=prefix, limit=1)
    if not entries:
        raise HTTPException(status_code=404, detail=f"Нет записей реестра для prefix '{prefix}'")
    e = entries[0]
    return MetricsSummaryResponse(
        prefix=e.get("prefix", prefix),
        metrics=e.get("metrics", {}),
        best_params=e.get("best_params", {}),
        trained_at=e.get("trained_at"),
        mlflow_run_id=e.get("mlflow_run_id"),
    )


# ---------------------------------------------------------------------------
# Retrain (async trigger)
# ---------------------------------------------------------------------------

@app.post("/retrain", response_model=RetrainResponse, tags=["training"])
async def trigger_retrain(req: RetrainRequest) -> RetrainResponse:
    """Запускает переобучение модели в фоновом потоке.

    Использует тот же механизм что и Streamlit UI (`frontend/services/trainer.py`),
    но вызывается напрямую через REST.
    Возвращает немедленно; следите за статусом через /registry или /metrics.
    """
    try:
        import psycopg2

        from backend.dataset.core import make_table_name
        from backend.model import load_training_data
        from backend.model.config import TRAIN_FRACTION
        from backend.model.report import load_grid_best_params
        from backend.model.config import DEFAULT_PARAM_VALUES

        symbol    = req.symbol.upper()
        timeframe = req.timeframe.lower()
        prefix    = f"catboost_{symbol.lower()}_{timeframe}"
        table_name = make_table_name(symbol, timeframe)

        db_config = {
            "host":     os.getenv("PGHOST",     "localhost"),
            "port":     int(os.getenv("PGPORT", "5432")),
            "dbname":   os.getenv("PGDATABASE", "crypt_date"),
            "user":     os.getenv("PGUSER",     ""),
            "password": os.getenv("PGPASSWORD", ""),
        }

        conn = psycopg2.connect(**db_config)
        try:
            X, y, feature_cols, timestamps = load_training_data(
                conn, table_name, target_col=req.target_col
            )
        finally:
            conn.close()

        if X is None or len(X) == 0:
            return RetrainResponse(
                status="error",
                prefix=prefix,
                message="Нет данных в таблице",
            )

        split    = int(len(X) * TRAIN_FRACTION)
        X_train  = X.iloc[:split]
        y_train  = y.iloc[:split]
        X_test   = X.iloc[split:]
        y_test   = y.iloc[split:]
        ts_test  = timestamps.iloc[split:]

        saved_best = load_grid_best_params(prefix, models_dir=MODELS_DIR)
        best_params = (
            saved_best["best_params"]
            if saved_best and saved_best.get("best_params")
            else {k: v[0] for k, v in DEFAULT_PARAM_VALUES.items()}
        )

        from backend.model.config import annualize_factor as _annualize_factor, timeframe_to_ms
        step_ms          = timeframe_to_ms(timeframe)
        annualize_factor = _annualize_factor(timeframe)

        import threading
        from backend.model import save_model, train_final_model
        from backend.model.mlflow_utils import log_session_to_mlflow
        from backend.model.report import register_model_version, save_session_result
        from backend.model.train import compute_overfitting_diagnostics

        def _bg() -> None:
            try:
                model, metrics, y_pred = train_final_model(
                    X_train, y_train, X_test, y_test,
                    best_params,
                    annualize_factor=annualize_factor,
                    use_gpu=req.use_gpu,
                )
                save_model(model, symbol, timeframe, models_dir=MODELS_DIR)

                try:
                    overfit_diag = compute_overfitting_diagnostics(
                        model, X_train, y_train, X_test, y_test,
                        feature_cols=feature_cols, step_ms=step_ms,
                    )
                except Exception:
                    overfit_diag = None

                save_session_result(
                    model, metrics, y_pred, y_test, ts_test,
                    feature_cols, best_params, overfit_diag,
                    prefix=prefix, target_col=req.target_col,
                )

                cbm_path = MODELS_DIR / f"{symbol}_{timeframe}.cbm"
                mlflow_run_id = log_session_to_mlflow(
                    enabled=req.mlflow_enabled,
                    tracking_uri=req.mlflow_uri,
                    experiment_name=req.mlflow_experiment,
                    run_name=prefix,
                    params=best_params,
                    metrics=metrics,
                    feature_cols=feature_cols,
                    model_path=cbm_path,
                    tags={"symbol": symbol, "timeframe": timeframe,
                          "target": req.target_col, "source": "api"},
                )

                register_model_version(
                    prefix, metrics, best_params, feature_cols,
                    models_dir=MODELS_DIR,
                    mlflow_run_id=mlflow_run_id,
                    target_col=req.target_col,
                    n_train=len(X_train),
                    n_test=len(X_test),
                )
                _LOG.info("[api] retrain %s завершён", prefix)
            except Exception as exc:
                _LOG.error("[api] retrain %s ошибка: %s", prefix, exc, exc_info=True)

        t = threading.Thread(target=_bg, daemon=True, name=f"api_retrain_{prefix}")
        t.start()

        return RetrainResponse(status="started", prefix=prefix, message="Обучение запущено в фоне")

    except Exception as exc:
        _LOG.error("[api] /retrain ошибка: %s", exc, exc_info=True)
        return RetrainResponse(status="error", prefix="", message=str(exc))


# ---------------------------------------------------------------------------
# Scheduler status
# ---------------------------------------------------------------------------

@app.get("/scheduler/status", response_model=SchedulerStatusResponse, tags=["scheduler"])
async def scheduler_status() -> SchedulerStatusResponse:
    if _scheduler is None:
        return SchedulerStatusResponse(running=False, jobs=[])
    jobs = [SchedulerJobInfo(**j) for j in _scheduler.list_jobs()]
    return SchedulerStatusResponse(running=True, jobs=jobs)
