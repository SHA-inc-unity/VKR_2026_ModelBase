"""Pydantic-схемы запросов и ответов FastAPI."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class RegistryEntry(BaseModel):
    version_id: str
    prefix: str
    trained_at: str
    target_col: str | None = None
    n_train: int = 0
    n_test: int = 0
    n_features: int = 0
    metrics: dict[str, float] = Field(default_factory=dict)
    best_params: dict[str, Any] = Field(default_factory=dict)
    mlflow_run_id: str | None = None


class RegistryResponse(BaseModel):
    entries: list[RegistryEntry]
    total: int


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------

class PredictionPoint(BaseModel):
    timestamp: str | int
    y_true: float
    y_pred: float
    direction_correct: bool


class PredictionsResponse(BaseModel):
    prefix: str
    saved_at: str
    n_samples: int
    metrics: dict[str, Any] | None = None
    best_params: dict[str, Any] | None = None
    predictions: list[PredictionPoint]


# ---------------------------------------------------------------------------
# Retrain (trigger)
# ---------------------------------------------------------------------------

class RetrainRequest(BaseModel):
    symbol: str = Field(..., examples=["BTCUSDT"])
    timeframe: str = Field(..., examples=["60m"])
    use_gpu: bool = False
    target_col: str = "target_return_1"
    cv_mode: str = "expanding"
    max_train_size: int = 0
    mlflow_enabled: bool = False
    mlflow_uri: str = "http://localhost:5000"
    mlflow_experiment: str = "ModelLine"


class RetrainResponse(BaseModel):
    status: str          # "started" | "already_running" | "error"
    prefix: str
    message: str = ""


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class SchedulerJobInfo(BaseModel):
    id: str
    name: str
    next_run: str | None = None


class SchedulerStatusResponse(BaseModel):
    running: bool
    jobs: list[SchedulerJobInfo]


# ---------------------------------------------------------------------------
# Metrics summary
# ---------------------------------------------------------------------------

class MetricsSummaryResponse(BaseModel):
    prefix: str
    metrics: dict[str, Any]
    best_params: dict[str, Any]
    trained_at: str | None = None
    mlflow_run_id: str | None = None
