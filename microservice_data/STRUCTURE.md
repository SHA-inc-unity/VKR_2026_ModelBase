# microservice_data — Структура

> Обновляй этот файл при каждом изменении модулей, классов или ключевых функций.

---

## Корень сервиса

| Файл | Описание |
|------|----------|
| `DataService.sln` | Solution-файл .NET |
| `Dockerfile` | Multi-stage сборка: `build` → `publish` → `runtime` (ASP.NET 8) |
| `docker-compose.yml` | Сервисы: `postgres` (порт 5433 host), `data` (порт 8100). Подключается к `modelline_net` (external) |
| `global.json` | Привязка SDK; `"rollForward": "latestMajor"` |
| `.env.example` | `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGPORT`, `DATA_API_PORT`, `KAFKA_BOOTSTRAP_SERVERS`, `MINIO_*` |

---

## src/DataService.API/

Единственный проект (ASP.NET Core Minimal API / Controllers). Clean Architecture пока не нужна — весь код здесь.

### Program.cs
Точка входа: регистрация DI, Serilog, Kafka consumer, MinIO, healthchecks, роутинг. *(Файл пока пустой — в разработке)*

### Settings/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `DataServiceSettings.cs` | `DataServiceSettings`, `PostgresSettings`, `KafkaSettings`, `MinioSettings`, `ApiSettings` | Strongly-typed конфиг из `appsettings.json` секции `"DataService"`. Переопределяется env-переменными (`DataService__Postgres__Host` и т.д.). `ConnectionString` строится автоматически |

### Controllers/

| Файл | Описание |
|------|----------|
| `HealthController.cs` | `GET /health` — liveness. `GET /ready` — readiness (проверяет Postgres). Используется Docker healthcheck |

### Database/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `PostgresConnectionFactory.cs` | `PostgresConnectionFactory` | Фабрика соединений Npgsql (pool). Инициализирует схему при старте |
| `DatasetRepository.cs` | `DatasetRepository`, `DatasetRepository.MarketRow` | CRUD для таблиц `{symbol}_{timeframe}` на схеме `timestamp_utc TIMESTAMPTZ PRIMARY KEY, symbol, exchange, timeframe, index_price, funding_rate, open_interest, rsi`. Методы: `CreateTableIfNotExistsAsync`, `GetCoverageAsync` / `GetCoverageIfExistsAsync` (`rows`, `min_ts_ms`, `max_ts_ms`), `FetchTimestampsAsync`, `FindMissingTimestampsAsync` (через `generate_series`), `FetchRowsAsync`, `ExportCsvAsync`, `BulkUpsertAsync` (UNNEST + `ON CONFLICT (timestamp_utc) DO UPDATE`, батчи по `DatasetConstants.UpsertBatchSize`), `DeleteRowsAsync(table, startMs?, endMs?)` — если оба timestamp-а `null` → `TRUNCATE TABLE` (возвращает число строк до очистки), иначе `DELETE WHERE timestamp_utc IN [start, end]`; для отсутствующей таблицы возвращает `0` без исключения. **Anomaly Inspect:** `GetColumnStatsAsync(table)` — возвращает `ColumnStatsResult(TotalRows, Columns[ColumnStat(Name, Dtype, NonNull, Min, Max, Mean, Std)])` одним SQL-запросом (information_schema.columns → динамический SELECT `COUNT(*)` + `COUNT("col")::bigint AS nn_{col}` + для численных типов `MIN/MAX/AVG/STDDEV_POP ::numeric`); имена идентификаторов проходят `Safe()`-гард. `GetColumnHistogramAsync(table, column, buckets)` — возвращает `HistogramResult(Column, Min, Max, Buckets[HistogramBucket(RangeStart, RangeEnd, Count)])`; buckets clamp 2..500; `width_bucket(col, lo, hi + (hi-lo)*1e-9, buckets)` с обрезкой индекса 0..buckets-1; пустые buckets дозаполняются нулями; при hi ≤ lo — один bucket с полным count; для полностью null-колонки — пустой массив buckets. Для отсутствующей таблицы оба метода возвращают `null`. |

### Dataset/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `DatasetCore.cs` | `DatasetCore` (static) | Port Python `backend/dataset/core.py`. Нормализация таймфрейма, построение имени таблицы, выравнивание временных окон, выбор OI-интервала |
| `DatasetConstants.cs` | `DatasetConstants` (static) | Таймфреймы (`Timeframes`, `TimeframeAliases`), интервалы OI, дефолты |

### Bybit/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `BybitApiClient.cs` | `BybitApiClient` | HTTP-клиент к Bybit REST v5 (`/linear`). Все три time-series метода используют одинаковый паттерн time-window параллелизации: диапазон нарезается на окна (размер = `PageLimit × intervalMs`), каждое окно — независимый запрос со `startTime`/`endTime`, без серверных курсоров; `Task.WhenAll` + `SemaphoreSlim(MaxParallelApiWorkers)`. Методы: `FetchInstrumentDetailsAsync` (кэш per-client), `FetchIndexPriceKlinesAsync(symbol, interval, startMs, endMs, stepMs, ...)`, `FetchFundingRatesAsync(symbol, startMs, endMs, fundingIntervalMs=28_800_000)`, `FetchOpenInterestAsync(symbol, intervalLabel, startMs, endMs, intervalMs)`. Все возвращают отсортированные по времени списки, дедуплицированные по timestamp. |

### Kafka/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `KafkaConsumerService.cs` | `KafkaConsumerService` (IHostedService) | Фоновый потребитель Kafka. Маршрутизирует `cmd.data.*` топики к use-case обработчикам. Устойчив к старту (resilient `SubscribeWithRetryAsync` с экспоненциальным backoff, `AllowAutoCreateTopics=true`, `SetErrorHandler` приглушает не-фатальные `UnknownTopicOrPart`/`LeaderNotAvailable`). Все обработчики используют безопасные аксессоры `TryGetString`/`TryGetInt64` вместо `GetProperty().GetString()` — отсутствующие поля возвращаются клиенту как `{ error: "missing fields: ..." }`, без `KeyNotFoundException` |
| `KafkaProducer.cs` | `KafkaProducer` | Обёртка над Confluent producer. `PublishReplyAsync` — reply с envelope `{correlation_id, payload}` (ключ = correlationId). `PublishEventAsync` — fire-and-forget публикация события без envelope (caller кладёт `correlation_id` внутрь payload); ошибки логируются как warning и не пробрасываются |
| `Topics.cs` | `Topics` (static) | Константы топиков Kafka. Имена строковых значений строго совпадают с `microservice_admin/src/lib/topics.ts` и реально созданными в Redpanda топиками: `cmd.data.db.ping`, `cmd.data.dataset.find_missing`, `cmd.data.dataset.table_schema`, `cmd.data.dataset.make_table_name`, `cmd.data.dataset.instrument_details`, `cmd.data.dataset.delete_rows`, `cmd.data.dataset.column_stats`, `cmd.data.dataset.column_histogram` (C#-идентификаторы `CmdDataDbPing`, `CmdDataDatasetMissing`, `CmdDataDatasetDeleteRows`, `CmdDataDatasetColumnStats`, `CmdDataDatasetColumnHistogram` и т.д.). |

### Minio/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `MinioClaimCheckService.cs` | `MinioClaimCheckService` | Реализация claim-check паттерна: загрузка/скачивание блобов (CSV-экспорт, инgest) в MinIO (S3) |

### HealthChecks/

| Файл | Описание |
|------|----------|
| `PostgresHealthCheck.cs` | ASP.NET IHealthCheck: пингует PostgreSQL |

### Infrastructure/ *(пусто)*

Зарезервировано для будущих репозиториев и сервисных абстракций.

---

## src/DataService.Application/

### UseCases/

Каждый use-case — отдельная папка. Все папки пока пусты (в разработке).

| Папка | Kafka-топик | Описание |
|-------|------------|----------|
| `GetCoverage/` | `cmd.data.dataset.coverage` | Диапазон дат и кол-во строк для символа/таймфрейма |
| `GetRows/` | `cmd.data.dataset.rows` | Срез строк (с пагинацией) |
| `ExportDataset/` | `cmd.data.dataset.export` | Экспорт в MinIO, возвращает URL (claim-check) |
| `IngestData/` | `cmd.data.dataset.ingest` | Полный pipeline: Bybit (klines + funding + OI параллельно) → forward-fill → Wilder RSI-14 (параллельно, `clamp(ProcessorCount,2,8)` сегментов с seed-ом состояния) → UNNEST-upsert только по отсутствующим timestamps. **Incremental fetch range**: klines грузятся на `[fetchStart, e]` (RSI warmup), но OI и funding — только на `[missingStart − intervalMs, missingEnd]`, где `missingStart/missingEnd` = мин/макс из `FindMissingTimestampsAsync`, а `intervalMs` берётся из `ChooseOpenInterestInterval(stepMs).IntervalMs` для OI и константного `28_800_000` (8 ч) для funding — это убирает лишние сотни параллельных запросов в Bybit. По ходу handler публикует события `events.data.ingest.progress` по 6 стадиям (`prepare`, `fetch_klines`, `fetch_funding`, `fetch_oi`, `compute_rsi`, `upsert`) со статусами `running` / `done` / `error` и полем `progress` 0..100 |
| `DeleteRows/` | `cmd.data.dataset.delete_rows` | Удаление строк по диапазону (`start_ms`, `end_ms` — оба обязательны) или полная очистка таблицы (оба null → `TRUNCATE`). Обрабатывается в `KafkaConsumerService.HandleDeleteRowsAsync` напрямую через `DatasetRepository.DeleteRowsAsync`. Ответ: `{ status, table, rows_deleted }` |
| `ColumnStats/` | `cmd.data.dataset.column_stats` | df.info()-style агрегаты по всем колонкам таблицы. Обрабатывается в `KafkaConsumerService.HandleColumnStatsAsync` через `DatasetRepository.GetColumnStatsAsync`. Один динамический SQL: `COUNT(*)` + per-column `COUNT("col")::bigint AS nn_{col}` + для численных типов (`numeric / double precision / real / integer / bigint / smallint`) `MIN/MAX/AVG/STDDEV_POP ::numeric AS min_/max_/avg_/std_{col}`. Ответ: `{ table, total_rows, columns: [{ name, dtype, non_null, null_count, null_pct, min, max, mean, std }] }`. Для отсутствующей таблицы — `{ error: "table not found" }`. |
| `ColumnHistogram/` | `cmd.data.dataset.column_histogram` | Гистограмма одной численной колонки. Обрабатывается в `KafkaConsumerService.HandleColumnHistogramAsync` через `DatasetRepository.GetColumnHistogramAsync`. Параметры: `table`, `column`, `buckets` (clamp 2..500, default 30). Использует `width_bucket(col, lo, hi + (hi−lo)*1e-9, buckets)` (правая граница exclusive), GROUP BY bucket, clamp индекса 0..buckets−1, пустые buckets дозаполняются нулями. При hi ≤ lo — один bucket с полным count. Ответ: `{ column, min, max, buckets: [{ range_start, range_end, count }] }`. |
| `GetTableSchema/` | `cmd.data.dataset.table_schema` | Схема таблицы (колонки, типы) |
| `GetTimestamps/` | `cmd.data.dataset.timestamps` | Список временных меток |
| `FindMissing/` | `cmd.data.dataset.find_missing` | Поиск пропусков в данных |
| `GetInstrument/` | `cmd.data.dataset.instrument_details` | Метаданные инструмента |

---

## src/DataService.Domain/

### Interfaces/ *(пусто)*

Будущие доменные интерфейсы репозиториев.

---

## Kafka-интерфейс

| Топик | Тип | Описание |
|-------|-----|----------|
| `cmd.data.health` | req/reply | Liveness + версия |
| `cmd.data.db.ping` | req/reply | Ping PostgreSQL |
| `cmd.data.dataset.list_tables` | req/reply | Список доступных таблиц |
| `cmd.data.dataset.coverage` | req/reply | `{exists, rows, min_ts_ms, max_ts_ms}` |
| `cmd.data.dataset.rows` | req/reply | Срез строк (paginated) |
| `cmd.data.dataset.export` | req/reply | Claim-check (MinIO URL) |
| `cmd.data.dataset.ingest` | req/reply | Bybit fetch → RSI-14 → upsert (см. README раздел «Ingest pipeline») |
| `cmd.data.dataset.delete_rows` | req/reply | Очистка таблицы (TRUNCATE при обоих `null`) или DELETE по `[start_ms, end_ms]`. Ответ: `{ status, table, rows_deleted }` |
| `cmd.data.dataset.column_stats` | req/reply | df.info()-стиль агрегаты по всем колонкам (Non-Null/Min/Max/Mean/Std + null_pct). Используется Anomaly Inspect. |
| `cmd.data.dataset.column_histogram` | req/reply | Гистограмма одной численной колонки (`width_bucket`, def. 30 buckets, clamp 2..500). Используется Anomaly Inspect при клике по строке таблицы. |
| `events.data.ingest.progress` | event (out) | Поэтапный прогресс ingest-а: `{ correlation_id, stage, label, status, progress, detail }` — потребляется frontend-ом через SSE |
| `cmd.data.dataset.normalize_timeframe` | req/reply | Нормализация строки таймфрейма |
| `cmd.data.dataset.make_table_name` | req/reply | Построение имени таблицы |
| `cmd.data.dataset.instrument_details` | req/reply | Метаданные инструмента |
| `cmd.data.dataset.table_schema` | req/reply | Схема колонок таблицы |
| `cmd.data.dataset.find_missing` | req/reply | Пропуски в данных |
| `cmd.data.dataset.timestamps` | req/reply | Список временных меток |
| `cmd.data.dataset.constants` | req/reply | Таймфреймы, дефолты |
| `events.data.dataset.updated` | event (out) | Данные обновлены после ingestion |
