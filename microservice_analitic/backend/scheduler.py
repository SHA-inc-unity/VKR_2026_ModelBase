"""Планировщик автоматического переобучения моделей (APScheduler).

Использование — запуск как самостоятельного процесса:
    python -m backend.scheduler

Или программно:
    from backend.scheduler import Scheduler
    sched = Scheduler.from_env()
    sched.start()          # non-blocking
    ...
    sched.stop()

Конфигурация через переменные окружения (или .env):
    SCHEDULER_JOBS         — JSON-список заданий (см. ниже)
    SCHEDULER_TIMEZONE     — tzdata-строка, по умолчанию "UTC"
    PGHOST / PGPORT / PGDATABASE / PGUSER / PGPASSWORD — PostgreSQL

Формат одного задания в SCHEDULER_JOBS:
    {
        "symbol":      "BTCUSDT",
        "timeframe":   "60m",
        "cron":        "0 3 * * *",        // cron-выражение (UTC)
        "use_gpu":     false,
        "target_col":  "target_return_1"
    }
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# APScheduler — опциональная зависимость
# ---------------------------------------------------------------------------

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _HAS_APSCHEDULER = True
except ImportError:
    BackgroundScheduler = None  # type: ignore[assignment,misc]
    CronTrigger = None          # type: ignore[assignment,misc]
    _HAS_APSCHEDULER = False


# ---------------------------------------------------------------------------
# Job definition
# ---------------------------------------------------------------------------

@dataclass
class SchedulerJob:
    symbol: str
    timeframe: str
    cron: str                          # e.g. "0 3 * * *"
    use_gpu: bool = False
    target_col: str = "target_return_1"
    cv_mode: str = "expanding"
    max_train_size: int = 0
    mlflow_enabled: bool = False
    mlflow_uri: str = "http://localhost:5000"
    mlflow_experiment: str = "ModelLine"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SchedulerJob":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in allowed})


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class Scheduler:
    """Обёртка над APScheduler для периодического переобучения моделей."""

    def __init__(
        self,
        jobs: list[SchedulerJob],
        db_config: dict[str, Any],
        models_dir: Path | None = None,
        timezone: str = "UTC",
    ) -> None:
        if not _HAS_APSCHEDULER:
            raise ImportError(
                "APScheduler не установлен. Запустите: pip install apscheduler"
            )
        self._jobs = jobs
        self._db_config = db_config
        self._models_dir = models_dir or (Path(__file__).parent.parent / "models")
        self._timezone = timezone
        self._scheduler = BackgroundScheduler(timezone=timezone)
        self._running = False

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "Scheduler":
        """Создаёт Scheduler из переменных окружения."""
        raw_jobs = os.getenv("SCHEDULER_JOBS", "[]")
        try:
            jobs_dicts: list[dict] = json.loads(raw_jobs)
        except json.JSONDecodeError as exc:
            _LOG.error("[scheduler] Неверный SCHEDULER_JOBS JSON: %s", exc)
            jobs_dicts = []

        jobs = [SchedulerJob.from_dict(d) for d in jobs_dicts]

        db_config = {
            "host":     os.getenv("PGHOST",     "localhost"),
            "port":     int(os.getenv("PGPORT",     "5432")),
            "database": os.getenv("PGDATABASE", "crypt_date"),
            "user":     os.getenv("PGUSER",     ""),
            "password": os.getenv("PGPASSWORD", ""),
        }

        timezone = os.getenv("SCHEDULER_TIMEZONE", "UTC")
        return cls(jobs=jobs, db_config=db_config, timezone=timezone)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Регистрирует задания и запускает планировщик (non-blocking)."""
        if self._running:
            _LOG.warning("[scheduler] уже запущен")
            return

        for job in self._jobs:
            self._scheduler.add_job(
                func=self._make_task(job),
                trigger=CronTrigger.from_crontab(job.cron, timezone=self._timezone),
                id=f"{job.symbol}_{job.timeframe}",
                name=f"retrain {job.symbol} {job.timeframe}",
                replace_existing=True,
                misfire_grace_time=3600,
            )
            _LOG.info(
                "[scheduler] задание зарегистрировано: %s %s  cron=%s",
                job.symbol, job.timeframe, job.cron,
            )

        self._scheduler.start()
        self._running = True
        _LOG.info("[scheduler] запущен (%d заданий)", len(self._jobs))

    def stop(self) -> None:
        """Останавливает планировщик."""
        if self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            _LOG.info("[scheduler] остановлен")

    def list_jobs(self) -> list[dict[str, Any]]:
        """Возвращает список зарегистрированных заданий с датой следующего запуска."""
        result = []
        for apjob in self._scheduler.get_jobs():
            result.append({
                "id":       apjob.id,
                "name":     apjob.name,
                "next_run": str(apjob.next_run_time) if apjob.next_run_time else None,
            })
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_task(self, job: SchedulerJob):
        """Возвращает callable для APScheduler, захватывающий job через closure."""

        def _task() -> None:
            _LOG.info(
                "[scheduler] → старт переобучения %s %s", job.symbol, job.timeframe
            )
            try:
                self._run_retrain(job)
            except Exception as exc:
                _LOG.error(
                    "[scheduler] ошибка переобучения %s %s: %s",
                    job.symbol, job.timeframe, exc,
                    exc_info=True,
                )

        return _task

    def _run_retrain(self, job: SchedulerJob) -> None:
        """Выполняет полный цикл переобучения для одного задания."""
        from backend.db import get_connection
        from backend.dataset.core import make_table_name
        from backend.model import (
            load_training_data,
            save_model,
            train_final_model,
        )
        from backend.model.config import MODELS_DIR
        from backend.model.mlflow_utils import log_session_to_mlflow
        from backend.model.report import register_model_version, save_session_result
        from backend.model.train import compute_overfitting_diagnostics

        models_dir = self._models_dir
        symbol     = job.symbol.upper()
        timeframe  = job.timeframe.lower()
        table_name = make_table_name(symbol, timeframe)

        # 1. Подключение к БД через единый пул (backend.db)
        with get_connection(self._db_config) as conn:
            X, y, feature_cols, timestamps = load_training_data(
                conn, table_name, target_col=job.target_col
            )

        if X is None or len(X) == 0:
            _LOG.warning("[scheduler] нет данных для %s — пропуск", table_name)
            return

        # 2. Walk-forward split (70/30)
        from backend.model.config import TRAIN_FRACTION
        split = int(len(X) * TRAIN_FRACTION)
        X_train, X_test = X.iloc[:split], X.iloc[split:]
        y_train, y_test = y.iloc[:split], y.iloc[split:]
        ts_test = timestamps.iloc[split:]

        # 3. Параметры: сохранённые Grid-результаты имеют приоритет над дефолтами
        from backend.model.config import DEFAULT_PARAM_VALUES
        # DEFAULT_PARAM_VALUES — dict[str, list]; берём первый элемент каждого списка
        best_params: dict = {k: v[0] for k, v in DEFAULT_PARAM_VALUES.items()}

        # Пытаемся загрузить ранее найденные лучшие параметры
        from backend.model.report import load_grid_best_params
        prefix = f"catboost_{symbol.lower()}_{timeframe}"
        saved_best = load_grid_best_params(prefix, models_dir=models_dir)
        if saved_best and saved_best.get("best_params"):
            best_params = saved_best["best_params"]
            _LOG.info("[scheduler] используются сохранённые Grid-параметры для %s", prefix)

        from backend.model.config import annualize_factor as _annualize_factor
        annualize_factor = _annualize_factor(timeframe)

        model, metrics, y_pred = train_final_model(
            X_train, y_train,
            X_test,  y_test,
            best_params,
            annualize_factor=annualize_factor,
            use_gpu=job.use_gpu,
        )
        _LOG.info("[scheduler] обучение завершено: %s", metrics)

        save_model(model, symbol, timeframe, models_dir=models_dir)

        # 4. Overfit diagnostics
        from backend.model.config import timeframe_to_ms as _timeframe_to_ms
        step_ms = _timeframe_to_ms(timeframe)
        try:
            overfit_diag = compute_overfitting_diagnostics(
                model, X_train, y_train, X_test, y_test,
                feature_cols=feature_cols,
                step_ms=step_ms,
            )
        except Exception:
            overfit_diag = None

        save_session_result(
            model, metrics, y_pred, y_test, ts_test,
            feature_cols, best_params, overfit_diag,
            prefix=prefix,
            target_col=job.target_col,
        )

        # 5. MLflow
        _cbm_path = models_dir / f"{symbol}_{timeframe}.cbm"
        mlflow_run_id = log_session_to_mlflow(
            enabled=job.mlflow_enabled,
            tracking_uri=job.mlflow_uri,
            experiment_name=job.mlflow_experiment,
            run_name=prefix,
            params=best_params,
            metrics=metrics,
            feature_cols=feature_cols,
            model_path=_cbm_path,
            tags={"symbol": symbol, "timeframe": timeframe, "target": job.target_col, "source": "scheduler"},
        )

        # 6. Registry
        register_model_version(
            prefix, metrics, best_params, feature_cols,
            models_dir=models_dir,
            mlflow_run_id=mlflow_run_id,
            target_col=job.target_col,
            n_train=len(X_train),
            n_test=len(X_test),
        )
        _LOG.info("[scheduler] ✓ переобучение %s завершено", prefix)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _timeframe_to_ms(tf: str) -> int:
    """Обёртка для обратной совместимости — делегирует в backend.model.config."""
    from backend.model.config import timeframe_to_ms
    return timeframe_to_ms(tf)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    """Точка входа: ``python -m backend.scheduler``."""
    _setup_logging()

    if not _HAS_APSCHEDULER:
        _LOG.error("APScheduler не установлен. Запустите: pip install apscheduler")
        sys.exit(1)

    scheduler = Scheduler.from_env()
    scheduler.start()

    _LOG.info("[scheduler] заданий: %d", len(scheduler.list_jobs()))
    for j in scheduler.list_jobs():
        _LOG.info("  %s  →  следующий запуск: %s", j["name"], j["next_run"])

    try:
        import time as _time
        while True:
            _time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.stop()
        _LOG.info("[scheduler] завершён")


if __name__ == "__main__":
    main()
