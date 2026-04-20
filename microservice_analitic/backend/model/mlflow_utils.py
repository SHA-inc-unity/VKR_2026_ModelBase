"""Опциональная интеграция с MLflow для логирования сессий обучения.

Модуль безопасно импортируется при отсутствии mlflow:
    ``_HAS_MLFLOW`` → False, все функции становятся no-op и возвращают None.

Типичное использование (из trainer.py):

    from backend.model.mlflow_utils import log_session_to_mlflow

    run_id = log_session_to_mlflow(
        enabled=True,
        tracking_uri="http://localhost:5000",
        experiment_name="ModelLine/btcusdt_60m",
        run_name=prefix,
        params=best_params,
        metrics=metrics,
        feature_cols=feature_cols,
        model_path=saved_cbm_path,
        tags={"symbol": "BTCUSDT", "timeframe": "60m", "target": target_col},
    )
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

try:
    import mlflow
    _HAS_MLFLOW = True
except ImportError:
    mlflow = None  # type: ignore[assignment]
    _HAS_MLFLOW = False


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def mlflow_available() -> bool:
    """True если пакет mlflow установлен."""
    return _HAS_MLFLOW


def log_session_to_mlflow(
    *,
    enabled: bool = True,
    tracking_uri: str = "http://localhost:5000",
    experiment_name: str = "ModelLine",
    run_name: str = "run",
    params: dict[str, Any],
    metrics: dict[str, Any],
    feature_cols: list[str] | None = None,
    model_path: "Path | None" = None,
    tags: "dict[str, str] | None" = None,
) -> "str | None":
    """Логирует завершённую сессию обучения в MLflow.

    Parameters
    ----------
    enabled:          Флаг включения. При False (или отсутствии mlflow) — no-op.
    tracking_uri:     URI трекинг-сервера (``http://...``) или ``mlruns/`` для локального.
    experiment_name:  Имя эксперимента в MLflow.
    run_name:         Имя конкретного run-а (обычно prefix модели).
    params:           Гиперпараметры (dict → mlflow.log_params).
    metrics:          Метрики (dict → mlflow.log_metric; только числовые значения).
    feature_cols:     Список признаков — логируется как текстовый артефакт.
    model_path:       Путь к сохранённому .cbm-файлу — логируется как артефакт.
    tags:             Произвольные строковые теги.

    Returns
    -------
    run_id : str | None
        MLflow run_id записанного run-а, или None если логирование не выполнялось.
    """
    if not enabled or not _HAS_MLFLOW:
        return None

    try:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)

        with mlflow.start_run(run_name=run_name) as run:
            # --- Гиперпараметры ---
            clean_params = {
                str(k): (str(v) if not isinstance(v, (int, float, str, bool)) else v)
                for k, v in params.items()
            }
            mlflow.log_params(clean_params)

            # --- Метрики (только числовые) ---
            for k, v in metrics.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    try:
                        mlflow.log_metric(k, float(v))
                    except Exception:
                        pass

            # --- Теги ---
            if tags:
                mlflow.set_tags({str(k): str(v) for k, v in tags.items()})

            # --- Список признаков ---
            if feature_cols:
                mlflow.log_text("\n".join(feature_cols), "feature_cols.txt")

            # --- .cbm артефакт ---
            if model_path is not None:
                _p = Path(model_path)
                if _p.exists():
                    mlflow.log_artifact(str(_p), artifact_path="model")

            run_id = run.info.run_id
            _LOG.info("[mlflow] run_id=%s  experiment=%s", run_id, experiment_name)
            return run_id

    except Exception as exc:
        _LOG.warning("[mlflow] Ошибка логирования: %s", exc)
        return None


def get_experiment_runs(
    experiment_name: str,
    tracking_uri: str = "http://localhost:5000",
    max_results: int = 100,
) -> "list[dict] | None":
    """Возвращает список последних run-ов эксперимента в виде list[dict].

    Каждый dict содержит: run_id, run_name, status, start_time, metrics, params.
    Возвращает None если mlflow недоступен или сервер не отвечает.
    """
    if not _HAS_MLFLOW:
        return None
    try:
        mlflow.set_tracking_uri(tracking_uri)
        client = mlflow.tracking.MlflowClient()
        exp = client.get_experiment_by_name(experiment_name)
        if exp is None:
            return []
        runs = client.search_runs(
            experiment_ids=[exp.experiment_id],
            max_results=max_results,
            order_by=["start_time DESC"],
        )
        result = []
        for r in runs:
            result.append({
                "run_id":    r.info.run_id,
                "run_name":  r.info.run_name or r.info.run_id[:8],
                "status":    r.info.status,
                "start_time": r.info.start_time,
                "metrics":   dict(r.data.metrics),
                "params":    dict(r.data.params),
                "tags":      {
                    k: v for k, v in r.data.tags.items()
                    if not k.startswith("mlflow.")
                },
            })
        return result
    except Exception as exc:
        _LOG.warning("[mlflow] get_experiment_runs ошибка: %s", exc)
        return None
