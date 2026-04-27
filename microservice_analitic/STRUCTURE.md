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
| `constants.py` | `TIMEFRAMES`, `RAW_TABLE_SCHEMA`, `FEATURE_TABLE_SCHEMA` | Таймфреймы, символы, лимиты страниц. `RAW_TABLE_SCHEMA` — 13 сырых колонок (вкл. OHLCV). `FEATURE_TABLE_SCHEMA` — 37 feature-колонок (вкл. atr, candle shape, volume features, rsi_slope). |
| `core.py` | `DatasetCore` | Загрузка, валидация, сохранение данных в PostgreSQL |
| `database.py` | `Database` | Kafka-обёртка: делегирует все запросы к данным в `microservice_data` через `data_client` |
| `features.py` | `FeatureEngineer` | Расчёт признаков (Pandas): MA, EMA, ATR, объёмы, лаги |
| `features_sql.py` | `FeatureEngineerSQL` | SQL-path расчёт признаков прямо в PostgreSQL (без Pandas). Генерирует 27 feature-колонок: `return_{1,6,24}`, `log_return_{1,6,24}`, `price_roll{6,24}_{mean,std,min,max}`, `price_to_roll{6,24}_mean`, `price_vol_{6,24}`, `oi_roll{6,24}_mean`, `oi_return_1`, `rsi_lag_{1,2}`, `hour_sin/cos`, `dow_sin/cos`. Депрекейтнуты и удалены: `price_lag_*`, `funding_rate_*`, `oi_lag_*`, `oi_to_funding`. |
| `pipeline.py` | `DatasetPipeline` | Оркестратор (Pandas-путь): загрузка → features → сохранение |
| `pipeline_sql.py` | `DatasetPipelineSQL` | Оркестратор (SQL-путь): более быстрый, без загрузки в память |
| `dataset_cache.py` | `DatasetCache`, `dataset_cache` (singleton) | In-memory кеш результатов запросов к БД. OOM-защита (лимит по числу записей + байтам + psutil free RAM). FIFO-эвикция |
| `export.py` | `export_to_csv()` | Экспорт датасета из PostgreSQL в CSV |
| `timelog.py` | `TimeLog` | Утилита логирования времени этапов пайплайна |
| `quality.py` | `OHLCV_RAW_COLUMNS`, `OHLCV_DERIVED_COLUMNS`, `RSI_DERIVED_COLUMNS`, `QualityGroup`, `QUALITY_GROUPS`, `_empty_report(table_name)`, `audit_dataset(table_name, request)` | Аудит заполненности датасета по трём группам колонок (OHLCV-сырые / Производные от OHLCV / Производные от RSI). Через `cmd.data.dataset.column_stats` собирает per-column non-null counts и считает `fill_pct = sum(non_null) * 100 / (total_rows * n_cols)` на группу. Запрос включает `"columns": [все 16 колонок QUALITY_GROUPS]` и `"count_only": True` — это ограничивает SQL только COUNT-агрегатами (без MIN/MAX/AVG/STDDEV), что значительно быстрее на больших таблицах. Пороги статуса: `≥99 → full`, `≥1 → partial`, `<1 → missing`. Если `column_stats` возвращает `{"error": "table not found"}` (или иное сообщение содержащее «not found»), `audit_dataset` возвращает валидный отчёт с `total_rows=0` и всеми группами `"missing"` — это не ошибка приложения, а факт «данные ещё не загружены». Каждая группа знает свой `repair_action` (`load_ohlcv` или `recompute_features`) — это связывает аудит с UI-кнопками admin-сервиса. |
| `repair.py` | `load_ohlcv(...)`, `recompute_features(...)`, `_fetch_klines_parallel(...)`, `_emit_progress(...)` | Pipeline-оркестратор для исправления выявленных проблем. `load_ohlcv` — `prepare` (`cmd.data.dataset.make_table`) → `fetch` (Bybit `/v5/market/kline` параллельно по `MAX_PARALLEL_API_WORKERS` окнам по `PAGE_LIMIT_KLINE=1000`, через `ThreadPoolExecutor`) → `upsert` (`cmd.data.dataset.upsert_ohlcv`, **батчами по `_UPSERT_BATCH_SIZE=4500` строк** — каждый батч ≈675 КБ JSON, заметно ниже 1 МБ aiokafka-лимита; таймаут на батч `max(300, batch_size/5000)` с, минимум **5 мин**; при ошибке любого батча операция немедленно прерывается; прогресс стадии отражает накопленный процент по всем батчам). `recompute_features` — `prepare` → `recompute` (`cmd.data.dataset.compute_features`, **адаптивный таймаут по таймфрейму**: `1m → 3600 с`, `3m/5m → 1800 с`, остальные → `_RECOMPUTE_TIMEOUT_DEFAULT=600 с`; словарь `_RECOMPUTE_TIMEOUT` + константа `_RECOMPUTE_TIMEOUT_DEFAULT`). Таймаут передаётся через `RequestFn = Callable[..., Awaitable[dict]]` c `**kwargs`; лямбды в `data_client.py` прозрачно проксируют `timeout=` в `KafkaClient.request`. Прогресс по стадиям публикуется в `events.analitic.dataset.repair.progress` через `client.send`. Возвращает `{ table, rows_*, elapsed_sec }` или `{ error }`. |

---

## backend/model/

Обучение, оценка и хранение CatBoost-моделей.

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `config.py` | `ModelConfig`, `TrainConfig`, `GridSearchConfig`, `MODELS_DIR` | Конфиги (Pydantic BaseSettings). `MODELS_DIR` — путь к папке `models/` |
| `train.py` | `ModelTrainer` | Обучение CatBoost: train/test split, fit, grid search, сохранение сессии (`.cbm` + `.json`) |
| `metrics.py` | `ModelMetrics`, `calc_metrics()` | MAE, RMSE, sign-accuracy, persistence-baseline, direction-accuracy |
| `loader.py` | `ModelLoader` | Загрузка / сохранение `.cbm`-файлов CatBoost с диска. `load_training_data()` и `load_training_data_from_rows()`: если в исходных данных отсутствует колонка-цель (`target_return_1`), автоматически вычисляют признаки и цель «на лету» через `backend.dataset.features.build_features(df, add_target=True)` — fallback для таблиц старого формата или raw-only dump'ов. |
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
| `data_client.py` | `get_rows()`, `get_timestamps()`, `find_missing()`, `get_coverage()` (возвращает `{rows, min_ts_ms, max_ts_ms}` или `None`), `get_schema()`, `make_table_name()`, `ingest()`, `db_ping()`; handlers `_handle_health`, `_handle_model_list` (отдает `{"models": load_registry(...)}` для админ-дэшборда), `_handle_dataset_load`, `_handle_dataset_unload`, `_handle_dataset_status`, `_handle_dbscan`, `_handle_quality_check`, `_handle_load_ohlcv`, `_handle_recompute_features` | Синхронный Kafka-клиент для доступа к данным через microservice_data; сам обслуживает `cmd.analytics.health` + `cmd.analytics.model.list` + 4 топика управления сессией датасета (`cmd.analitic.dataset.{load,unload,status}`, `cmd.analitic.anomaly.dbscan`) + 3 топика аудита/исправления качества (`cmd.analitic.dataset.{quality_check, load_ohlcv, recompute_features}`). `_handle_quality_check` лениво импортирует `backend.dataset.quality.audit_dataset` и пробрасывает в неё `request=lambda topic, p: client.request(topic, p, timeout=45.0)` — явный таймаут 45 с предотвращает каскадный таймаут (внешний handler ограничен 60 с на стороне фронта). `_handle_load_ohlcv` / `_handle_recompute_features` лениво импортируют `backend.dataset.repair` и передают `request=lambda topic, p, **kw: client.request(topic, p, **kw)` (прозрачная передача `timeout=`) и `publish=lambda topic, env: client.send(topic, env)` — последний используется для эмита `events.analitic.dataset.repair.progress`. `_handle_dataset_load` запрашивает `cmd.data.dataset.export` (presigned URL → MinIO ZIP), стримит в tmp CSV (`httpx.AsyncClient(http2=True)` 1 MB-чанками), магически детектит ZIP по сигнатуре `PK\x03\x04` и распаковывает, затем `pandas.read_csv(chunksize=50_000)` → cast float64→float32 → `pyarrow.ParquetWriter(snappy)` append per chunk на диск (`SESSION_DIR`). `_handle_dbscan` читает только нужные колонки через `pd.read_parquet(columns=…)`, систематически сэмплит до `max_sample_rows`, `StandardScaler` + `DBSCAN.fit_predict`, чистит память `del df, sample; gc.collect()`. |

---

## backend/anomaly/

Постоянная dataset-сессия + multivariate anomaly detection.

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `__init__.py` | re-export `DatasetSession`, `get_session`, `MAX_SESSION_ROWS` | Точка входа подпакета |
| `session.py` | `_Meta` (dataclass), `DatasetSession` (singleton), `get_session()`, `reset_session_dir()`, `MAX_SESSION_ROWS=5_000_000`, `SESSION_DIR=Path(env MODELLINE_SESSION_DIR or "/tmp/modelline_sessions")` | Потокобезопасная (`threading.Lock`) сессия датасета. `set(symbol, timeframe, table_name, parquet_path, row_count, memory_mb_on_disk)` — атомарно меняет meta, удаляет старый Parquet. `clear()` — unlink + `gc.collect()`. `get_metadata()` отдаёт dict без `parquet_path`. `is_loaded_for(symbol, timeframe)` — проверка совпадения. `_silent_unlink` глотает `OSError`. |
| `isolation_forest.py` | `handle_isolation_forest(envelope)`, `DEFAULT_COLUMNS`, `DEFAULT_CONTAM=0.01`, `DEFAULT_TREES=100`, `DEFAULT_MAX_ROWS=50_000`, `_coerce_float`, `_coerce_int` | Tree-based outlier detector (`sklearn.ensemble.IsolationForest`, `n_jobs=-1`, `random_state=42`). Параметры: `contamination` (`[1e-4, 0.5]`), `n_estimators` (`[20, 500]`), `max_sample_rows` (`[1000, 1_000_000]`). Систематический сэмплинг (`df.iloc[::step]`) сохраняет временной порядок. Eager-cleanup `del df, sample; gc.collect()`. Регистрируется в `data_client._ensure_client()` под `cmd.analitic.anomaly.isolation_forest`. |
| `distribution.py` | `handle_distribution(envelope)`, `DEFAULT_BINS=50`, `DEFAULT_COL='close_price'`, `JB_MIN_SAMPLES=2_000`, `_verdict(kurtosis, jb_p, n)` | Диагностика распределения log-доходностей. Считает `scipy.stats.skew(bias=False)`, `scipy.stats.kurtosis(fisher=True, bias=False)`, `scipy.stats.jarque_bera`. Гистограмма клипуется в ±5σ; нормальная overlay-кривая `stats.norm.pdf(centres) * n * bin_width` отскейлена в expected counts per bin. `_verdict` возвращает текстовое заключение (heavy tails / normal / sample too small). Регистрируется как `cmd.analitic.dataset.distribution`. |

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
| `cmd.data.dataset.export` | Получить presigned URL на ZIP/CSV из MinIO (используется при `_handle_dataset_load`) |
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
| `cmd.analitic.dataset.load` | req/reply | Загрузить датасет в постоянную сессию: запрос `cmd.data.dataset.export` → стрим CSV/ZIP по presigned URL → Parquet на диск (snappy). Payload: `{ symbol, timeframe }`. Ответ: `{ loaded, symbol, timeframe, table_name, row_count, memory_mb_on_disk, loaded_at }` или `{ error: "table_not_found" \| "empty_table" \| "row_count_exceeds_limit" }`. Лимит: `MAX_SESSION_ROWS=5_000_000`. |
| `cmd.analitic.dataset.unload` | req/reply | Очистить сессию (unlink Parquet + `gc.collect`). Ответ: `{ cleared: true }` |
| `cmd.analitic.dataset.status` | req/reply | Получить состояние сессии. Ответ: `{ loaded: bool, ...meta }` (без `parquet_path`) |
| `cmd.analitic.anomaly.dbscan` | req/reply | Multivariate DBSCAN на загруженной сессии. Параметры: `eps=0.5`, `min_samples=5`, `max_sample_rows=50_000`, `columns=[close_price,volume,turnover,open_interest]`. Читает только нужные колонки через `pd.read_parquet(columns=…)`, систематический сэмпл `df.iloc[::step]`, `StandardScaler` + `DBSCAN.fit_predict`. Ответ: `{ summary: { total_rows, sample_size, n_clusters, n_anomalies, eps, min_samples, columns }, anomaly_timestamps_ms: [...] }`. После — `del df, sample; gc.collect()`. |
