# microservice_analitic — Структура

> Обновляй этот файл при каждом изменении модулей, классов или ключевых функций.

---

## Корень сервиса

| Файл | Описание |
|------|----------|
| `Dockerfile.base` | Базовый образ (Python + requirements). FROM python:3.12-slim |
| `Dockerfile.api` | FastAPI-сервер; `FROM base` |
| `docker-compose.yml` | Сервисы: `base` (profile `build-base`), `api`, `scheduler` (profile `scheduler`), `redis`. Сеть: `modelline_net` (внешняя) |
| `requirements.txt` | Python-зависимости (CatBoost, FastAPI, APScheduler, ReportLab, aiokafka…) |
| `.env.example` | Шаблон: `KAFKA_BOOTSTRAP_SERVERS`, `API_HOST/PORT`, `SCHEDULER_*` |

---

## backend/api/

Точка входа FastAPI. Все эндпоинты здесь.

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `app.py` | `app` (FastAPI), `_lifespan()` | Создание приложения, CORS, подключение роутеров. Автозапуск `Scheduler` если `SCHEDULER_AUTOSTART=true`. Эндпоинты: `GET /health`, `GET /registry`, `DELETE /registry/{version_id}`, `GET /predictions/{prefix}`, `GET /metrics/{prefix}`, `POST /retrain`, `GET /scheduler/status` |
| `schemas.py` | `HealthResponse`, `RegistryEntry`, `RegistryResponse`, `PredictionPoint`, `PredictionsResponse`, `RetrainRequest`, `RetrainResponse`, `MetricsSummaryResponse`, `SchedulerJobInfo`, `SchedulerStatusResponse` | Pydantic-схемы запросов и ответов |
| `run.py` | — | Точка входа uvicorn |

---

## backend/dataset/

Загрузка, хранение и feature engineering рыночных данных.

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `api.py` | `DatasetApi` | HTTP-клиент к Bybit API: исторические свечи, open interest |
| `constants.py` | `TIMEFRAMES`, `DEFAULT_SYMBOL`, `OpenInterestIntervals` | Допустимые таймфреймы, символы, лимиты страниц |
| `core.py` | `DatasetCore` | Загрузка, валидация, сохранение данных в PostgreSQL |
| `database.py` | `Database` | Kafka-обёртка: делегирует все запросы к данным в `microservice_data` через `data_client` |
| `features.py` | `FeatureEngineer` | Расчёт признаков (Pandas): MA, EMA, ATR, объёмы, лаги |
| `features_sql.py` | `FeatureEngineerSQL` | SQL-path расчёт признаков прямо в PostgreSQL (без Pandas) |
| `pipeline.py` | `DatasetPipeline` | Оркестратор (Pandas-путь): загрузка → features → сохранение |
| `pipeline_sql.py` | `DatasetPipelineSQL` | Оркестратор (SQL-путь): более быстрый, без загрузки в память |
| `dataset_cache.py` | `DatasetCache`, `dataset_cache` (singleton) | In-memory кеш результатов запросов к БД. OOM-защита (лимит по числу записей + байтам + psutil free RAM). FIFO-эвикция |
| `export.py` | `export_to_csv()` | Экспорт датасета из PostgreSQL в CSV |
| `timelog.py` | `TimeLog` | Утилита логирования времени этапов пайплайна |

---

## backend/model/

Обучение, оценка и хранение CatBoost-моделей.

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `config.py` | `ModelConfig`, `TrainConfig`, `GridSearchConfig`, `MODELS_DIR` | Конфиги (Pydantic BaseSettings). `MODELS_DIR` — путь к папке `models/` |
| `train.py` | `ModelTrainer` | Обучение CatBoost: train/test split, fit, grid search, сохранение сессии (`.cbm` + `.json`) |
| `metrics.py` | `ModelMetrics`, `calc_metrics()` | MAE, RMSE, sign-accuracy, persistence-baseline, direction-accuracy |
| `loader.py` | `ModelLoader` | Загрузка / сохранение `.cbm`-файлов CatBoost с диска |
| `cache.py` | `ModelCache` | In-memory кеш обученных моделей и их метаданных |
| `report.py` | `ReportBuilder`, `load_registry()`, `delete_registry_version()` | Сборка JSON-отчёта версии, запись в `models/`, реестр версий |
| `pdf_report.py` | `PdfReportGenerator` | Генерация PDF-отчёта с метриками и графиками (ReportLab) |
| `mlflow_utils.py` | `log_session_to_mlflow()`, `_HAS_MLFLOW` | Опциональная интеграция с MLflow. Безопасно импортируется без mlflow (no-op). Логирует параметры, метрики, модель `.cbm` |

---

## backend/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `scheduler.py` | `Scheduler`, `setup_scheduler()` | APScheduler-задачи: автообновление датасета, переобучение по cron. Данные через `data_client` (Kafka) |
| `utils.py` | `get_logger()`, `format_duration()` | Логирование, форматирование времени, вспомогательные утилиты |
| `data_client.py` | `get_rows()`, `get_timestamps()`, `find_missing()`, `get_coverage()` (возвращает `{rows, min_ts_ms, max_ts_ms}` или `None`), `get_schema()`, `make_table_name()`, `ingest()`, `db_ping()`; handlers `_handle_health`, `_handle_model_list` (отдает `{"models": load_registry(...)}` для админ-дэшборда) | Синхронный Kafka-клиент для доступа к данным через microservice_data; сам обслуживает `cmd.analytics.health` + `cmd.analytics.model.list` |

---

## frontend/

> UI переехал в `microservice_admin` (Next.js). Папка `frontend/` устарела и не используется.

---

## tests/

| Файл | Описание |
|------|----------|
| `conftest.py` | Фикстуры pytest: мок-конфиги, мок-БД |
| `test_api_mocked.py` | Тесты FastAPI-эндпоинтов (httpx AsyncClient, моки) |
| `test_cache.py` | Тесты `ModelCache` |
| `test_cache_extra.py` | Дополнительные кейсы ModelCache |
| `test_config_expand.py` | Тесты `ModelConfig` расширения/валидации |
| `test_core.py` | Тесты `DatasetCore` |
| `test_csv_io.py` | Тесты `csv_io` |
| `test_database_mocked.py` | Тесты `Database` с мок-asyncpg |
| `test_dataset_cache.py` | Тесты `DatasetCache` (OOM-логика, FIFO) |
| `test_db_module.py` | Тесты `db.py` |
| `test_export.py` | Тесты `export.py` |
| `test_features.py` | Тесты `FeatureEngineer` (Pandas-путь) |
| `test_features_sql.py` | Тесты `FeatureEngineerSQL` (SQL-путь) |
| `test_loader_mocked.py` | Тесты `ModelLoader` с мок-файловой системой |
| `test_metrics.py` | Тесты расчёта метрик |
| `test_pdf_report.py` / `_extra.py` | Тесты генерации PDF |
| `test_perf_stage.py` | Перформанс-тест ключевых этапов |
| `test_pipeline_pure.py` | Тесты `DatasetPipeline` без БД |
| `test_registry.py` | Тесты реестра версий моделей |
| `test_report_funcs.py` | Тесты `ReportBuilder` |
| `test_session_roundtrip.py` | Round-trip: сохранение → загрузка сессии модели |
| `test_train_utils.py` | Тесты утилит обучения |
| `test_utils.py` | Тесты `utils.py` |

---

## models/ (runtime-артефакты, не в git)

Хранит `.cbm`-файлы, JSON-отчёты и реестр версий. Путь задаётся `MODELS_DIR`.

---

## Kafka-интерфейс

Сервис обменивается со смежными сервисами через Kafka (`modelline_net`).

### Исходящие запросы → microservice_data (`cmd.data.dataset.*`)

| Топик | Описание |
|-------|-----------|
| `cmd.data.dataset.rows` | Получить строки в диапазоне |
| `cmd.data.dataset.timestamps` | Получить временные метки |
| `cmd.data.dataset.find_missing` | Найти пропущенные метки |
| `cmd.data.dataset.coverage` | Метаданные (min/max/count) |
| `cmd.data.dataset.table_schema` | Схема колонок |
| `cmd.data.dataset.make_table_name` | Каноническое имя таблицы |
| `cmd.data.dataset.ingest` | Запустить загрузку исторических данных из Bybit |
| `cmd.data.db.ping` | Health-пинг БД |

### Входящие команды (`cmd.analytics.*`)

| Топик | Тип | Описание |
|-------|-----|----------|
| `cmd.analytics.health` | req/reply | Liveness |
| `cmd.analytics.train.start` | req/reply | Запуск обучения |
| `cmd.analytics.train.status` | req/reply | Статус обучения |
| `cmd.analytics.model.list` | req/reply | Список версий моделей из `models/registry.json` (обрабатывается `data_client._handle_model_list`; ответ `{"models": [...]}`) |
| `cmd.analytics.model.load` | req/reply | Загрузка модели |
| `cmd.analytics.predict` | req/reply | Прогноз |
| `events.analytics.train.progress` | event (out) | Прогресс обучения |
| `events.analytics.model.ready` | event (out) | Модель обучена |
