# microservice_data — Структура

> Обновляй этот файл при каждом изменении модулей, классов или ключевых функций.

---

## Корень сервиса

| Файл | Описание |
|------|----------|
| `DataService.sln` | Solution-файл .NET |
| `Dockerfile` | Multi-stage сборка: `build` → `publish` → `runtime` (ASP.NET 8) |
| `docker-compose.yml` | Сервисы: `postgres` (порт 5433 host), `data` (порт 8100). Подключается к `modelline_net` (external). Env `MINIO_PUBLIC_URL` (default `http://localhost:9000`) — внешний базовый URL MinIO, подставляемый в presigned URL для браузера (внутренний `MINIO_ENDPOINT=http://minio:9000` не резолвится с хоста). |
| `global.json` | Привязка SDK; `"rollForward": "latestMajor"` |
| `.env.example` | `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGPORT`, `DATA_API_PORT`, `KAFKA_BOOTSTRAP_SERVERS`, `MINIO_*`, `MINIO_PUBLIC_URL` |

---

## src/DataService.API/

Единственный проект (ASP.NET Core Minimal API / Controllers). Clean Architecture пока не нужна — весь код здесь.

### Program.cs
Точка входа: регистрация DI, Serilog, Kafka consumer, MinIO, healthchecks, роутинг. *(Файл пока пустой — в разработке)*

### Settings/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `DataServiceSettings.cs` | `DataServiceSettings`, `PostgresSettings`, `KafkaSettings`, `MinioSettings`, `ApiSettings` | Strongly-typed конфиг из `appsettings.json` секции `"DataService"`. Переопределяется env-переменными (`DataService__Postgres__Host` и т.д.). `ConnectionString` строится автоматически. `MinioSettings` содержит `Endpoint` (внутренний, напр. `http://minio:9000`) и `PublicUrl` (внешний, подставляется в presigned URL для браузера; env `MINIO_PUBLIC_URL`). |

### Controllers/

| Файл | Описание |
|------|----------|
| `HealthController.cs` | `GET /health` — liveness. `GET /ready` — readiness (проверяет Postgres). Используется Docker healthcheck |

### Database/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `PostgresConnectionFactory.cs` | `PostgresConnectionFactory` | Фабрика соединений Npgsql (pool). Инициализирует схему при старте |
| `DatasetRepository.cs` | `DatasetRepository`, `DatasetRepository.MarketRow` | CRUD для таблиц `{symbol}_{timeframe}`. Схема сырых данных (13 колонок): `timestamp_utc TIMESTAMPTZ PK, symbol, exchange, timeframe, index_price, open_price, high_price, low_price, volume, turnover, funding_rate, open_interest, rsi`. `MarketRow` — record из 13 полей. `BulkUpsertAsync` — UNNEST + `ON CONFLICT (timestamp_utc) DO UPDATE`, 13 параметров, батчи по `DatasetConstants.UpsertBatchSize`. `ComputeAndUpdateFeaturesAsync` — двухуровневый CTE (tr_cte для ATR) + `UPDATE` 37 feature-колонок (вкл. OHLCV-признаки). `GetColumnStatsAsync`, `GetColumnHistogramAsync` — Anomaly Inspect API. | Методы: `CreateTableIfNotExistsAsync`, `GetCoverageAsync` / `GetCoverageIfExistsAsync` (`rows`, `min_ts_ms`, `max_ts_ms`), `FetchTimestampsAsync`, `FindMissingTimestampsAsync` (через `generate_series`), `FetchRowsAsync`, `ExportCsvToStreamAsync(table, startMs, endMs, Stream output, ct)` — **streaming CSV**: `NpgsqlConnection.BeginTextExport` с `COPY (SELECT <cols> , (EXTRACT(EPOCH FROM timestamp_utc)*1000)::bigint AS timestamp_ms FROM ... WHERE ... ORDER BY timestamp_utc) TO STDOUT WITH CSV HEADER`; возвращаемый `TextReader` переливается в `output` через `StreamWriter(UTF8, 64 KB)` кусками по ~32 K символов — нет `List<dynamic>`, нет `StringBuilder`, нет промежуточного `byte[]`. Имя колонки `timestamp_utc` в SELECT заменяется на вычисляемое `timestamp_ms` (контракт Kafka), идентификатор таблицы проходит `Safe()`. Используется `HandleExportAsync` в связке с `System.IO.Pipelines`. `BulkUpsertAsync` (UNNEST + `ON CONFLICT (timestamp_utc) DO UPDATE`, батчи по `DatasetConstants.UpsertBatchSize`), `DeleteRowsAsync(table, startMs?, endMs?)` — если оба timestamp-а `null` → `TRUNCATE TABLE` (возвращает число строк до очистки), иначе `DELETE WHERE timestamp_utc IN [start, end]`; для отсутствующей таблицы возвращает `0` без исключения. **Anomaly Inspect:** `GetColumnStatsAsync(table)` — возвращает `ColumnStatsResult(TotalRows, Columns[ColumnStat(Name, Dtype, NonNull, Min, Max, Mean, Std)])` одним SQL-запросом (information_schema.columns → динамический SELECT `COUNT(*)` + `COUNT("col")::bigint AS nn_{col}` + для численных типов `MIN/MAX/AVG/STDDEV_POP ::numeric`); имена идентификаторов проходят `Safe()`-гард. `GetColumnHistogramAsync(table, column, buckets)` — возвращает `HistogramResult(Column, Min, Max, Buckets[HistogramBucket(RangeStart, RangeEnd, Count)])`; buckets clamp 2..500; `width_bucket(col, lo, hi + (hi-lo)*1e-9, buckets)` с обрезкой индекса 0..buckets-1; пустые buckets дозаполняются нулями; при hi ≤ lo — один bucket с полным count; для полностью null-колонки — пустой массив buckets. Для отсутствующей таблицы оба метода возвращают `null`. |

### Dataset/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `DatasetCore.cs` | `DatasetCore` (static) | Port Python `backend/dataset/core.py`. Нормализация таймфрейма, построение имени таблицы, выравнивание временных окон, выбор OI-интервала |
| `DatasetConstants.cs` | `DatasetConstants` (static) | Таймфреймы, интервалы OI, `RawTableSchema` (13 колонок), `BuildFeatureTableSchema()` (37 фич колонок), `FullTableSchema` (50 всего), дефолты лимитов |

### Bybit/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `BybitApiClient.cs` | `BybitApiClient` | HTTP-клиент к Bybit REST v5 (`/linear`). Все time-series методы используют одинаковый паттерн time-window параллелизации: диапазон нарезается на окна (размер = `PageLimit × intervalMs`), каждое окно — независимый запрос со `startTime`/`endTime`, без серверных курсоров; `Task.WhenAll` + `SemaphoreSlim(MaxParallelApiWorkers)`. Методы: `FetchInstrumentDetailsAsync` (кэш per-client), `FetchKlinesAsync(symbol, interval, startMs, endMs, stepMs, ...)` — возвращает `(TimestampMs, Open, High, Low, Close, Volume, Turnover)` с эндпоинта `/v5/market/kline`, `FetchFundingRatesAsync`, `FetchOpenInterestAsync`. Все возвращают отсортированные по времени списки, дедуплицированные по timestamp. |

### Kafka/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `KafkaConsumerService.cs` | `KafkaConsumerService` (IHostedService) | Фоновый потребитель Kafka. Маршрутизирует `cmd.data.*` топики к use-case обработчикам. Устойчив к старту (resilient `SubscribeWithRetryAsync` с экспоненциальным backoff, `AllowAutoCreateTopics=true`, `SetErrorHandler` приглушает не-фатальные `UnknownTopicOrPart`/`LeaderNotAvailable`). Все обработчики используют безопасные аксессоры `TryGetString`/`TryGetInt64` вместо `GetProperty().GetString()` — отсутствующие поля возвращаются клиенту как `{ error: "missing fields: ..." }`, без `KeyNotFoundException` |
| `KafkaProducer.cs` | `KafkaProducer` | Обёртка над Confluent producer. `PublishReplyAsync` — reply с envelope `{correlation_id, payload}` (ключ = correlationId). `PublishEventAsync` — fire-and-forget публикация события без envelope (caller кладёт `correlation_id` внутрь payload); ошибки логируются как warning и не пробрасываются |
| `Topics.cs` | `Topics` (static) | Константы топиков Kafka. Имена строковых значений строго совпадают с `microservice_admin/src/lib/topics.ts` и реально созданными в Redpanda топиками: `cmd.data.db.ping`, `cmd.data.dataset.find_missing`, `cmd.data.dataset.table_schema`, `cmd.data.dataset.make_table_name`, `cmd.data.dataset.instrument_details`, `cmd.data.dataset.delete_rows`, `cmd.data.dataset.column_stats`, `cmd.data.dataset.column_histogram`, `cmd.data.dataset.browse` (C#-идентификаторы `CmdDataDbPing`, `CmdDataDatasetMissing`, `CmdDataDatasetDeleteRows`, `CmdDataDatasetColumnStats`, `CmdDataDatasetColumnHistogram`, `CmdDataDatasetBrowse` и т.д.). |

### Minio/

| Файл | Ключевые объекты | Описание |
|------|-----------------|----------|
| `MinioClaimCheckService.cs` | `MinioClaimCheckService` | S3/MinIO-клиент (AWSSDK.S3 3.*). `PutBytesAsync` / `GetBytesAsync` — прежний claim-check для мелких payload-ов (строк JSON из `HandleRowsAsync`). **Потоковая связка для CSV-экспорта**: `PutStreamAsync(Stream, key, contentType, ct)` — загрузка произвольного по длине потока через `TransferUtility.UploadAsync` + `TransferUtilityUploadRequest { AutoResetStreamPosition=false, AutoCloseStream=false, PartSize=5 MB }` (автоматически переключается на multipart upload по мере поступления данных, не требует content-length). `GetPresignedUrlAsync(key, publicBaseUrl, expiresMinutes, downloadFilename?, contentType?, ct)` — presigned GET URL через `GetPreSignedUrlRequest` с `ResponseHeaderOverrides.ContentDisposition="attachment; filename=\"{file}\""` и `ContentType="text/csv; charset=utf-8"`; полученный URL переписывается с внутреннего хоста (`cfg.Endpoint`, `http://minio:9000`) на `publicBaseUrl` (`cfg.PublicUrl`, `http://localhost:9000`) — подпись SigV4 остаётся валидной, MinIO не привязывает её к `Host`-хедеру. TTL=60 мин → объекты «истекают» без явной очистки. |

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
| `ExportDataset/` | `cmd.data.dataset.export` | Два режима экспорта по shape payload-а. **Single-table** (`{ table, start_ms, end_ms }`): потоковый pipeline — `KafkaConsumerService.HandleExportAsync` открывает `System.IO.Pipelines.Pipe`, параллельно запускает два `Task.Run`: writer → `DatasetRepository.ExportCsvToStreamAsync` (PostgreSQL `COPY (...) TO STDOUT WITH CSV HEADER` через `BeginTextExport` → `StreamWriter` в `pipe.Writer.AsStream(leaveOpen:true)`), reader → `MinioClaimCheckService.PutStreamAsync` из `pipe.Reader.AsStream(leaveOpen:true)` (multipart upload). Стороны pipe закрываются явно через `CompleteAsync(captured)` — ошибка не теряется при dispose стрима. Ключ объекта — `exports/{guid}.csv`. После `Task.WhenAll` вызывается `MinioClaimCheckService.GetPresignedUrlAsync(key, MinioPublicUrl, 60, "<table>.csv", "text/csv; charset=utf-8")`; ответ: `{ presigned_url }`. Память: ~64 KB буфер pipe + 5 MB multipart part. **Multi-table ZIP** (`{ tables: string[], start_ms, end_ms }`): handler создаёт `MemoryStream` + `ZipArchive(ms, ZipArchiveMode.Create, leaveOpen:true)`; для каждой таблицы `archive.CreateEntry("{table}.csv", CompressionLevel.Optimal)` → `entry.Open()` → `_repo.ExportCsvToStreamAsync(table, startMs, endMs, entryStream, ct)` — отдельные CSV не буферизуются, только итоговый сжатый ZIP. После `archive.Dispose()` (запись central-directory) bytes из MemoryStream загружаются через `PutBytesAsync(key=exports/{guid}.zip, contentType="application/zip")` → ответ `{ claim_check }`. Нужен чтобы обойти ограничение Chromium, который подавляет программные multi-file загрузки. |
| `IngestData/` | `cmd.data.dataset.ingest` | Полный pipeline: Bybit (klines + funding + OI параллельно) → forward-fill → Wilder RSI-14 (параллельно, `clamp(ProcessorCount,2,8)` сегментов с seed-ом состояния) → UNNEST-upsert только по отсутствующим timestamps. **Incremental fetch range**: klines грузятся на `[fetchStart, e]` (RSI warmup), но OI и funding — только на `[missingStart − intervalMs, missingEnd]`, где `missingStart/missingEnd` = мин/макс из `FindMissingTimestampsAsync`, а `intervalMs` берётся из `ChooseOpenInterestInterval(stepMs).IntervalMs` для OI и константного `28_800_000` (8 ч) для funding — это убирает лишние сотни параллельных запросов в Bybit. По ходу handler публикует события `events.data.ingest.progress` по 6 стадиям (`prepare`, `fetch_klines`, `fetch_funding`, `fetch_oi`, `compute_rsi`, `upsert`) со статусами `running` / `done` / `error` и полем `progress` 0..100 |
| `DeleteRows/` | `cmd.data.dataset.delete_rows` | Удаление строк по диапазону (`start_ms`, `end_ms` — оба обязательны) или полная очистка таблицы (оба null → `TRUNCATE`). Обрабатывается в `KafkaConsumerService.HandleDeleteRowsAsync` напрямую через `DatasetRepository.DeleteRowsAsync`. Ответ: `{ status, table, rows_deleted }` |
| `ColumnStats/` | `cmd.data.dataset.column_stats` | df.info()-style агрегаты по всем колонкам таблицы. Обрабатывается в `KafkaConsumerService.HandleColumnStatsAsync` через `DatasetRepository.GetColumnStatsAsync`. Один динамический SQL: `COUNT(*)` + per-column `COUNT("col")::bigint AS nn_{col}` + для численных типов (`numeric / double precision / real / integer / bigint / smallint`) `MIN/MAX/AVG/STDDEV_POP ::float8 AS min_/max_/avg_/std_{col}` (каст в `double precision`, чтобы избежать `System.Decimal` overflow). Ответ: `{ table, total_rows, columns: [{ name, dtype, non_null, null_count, null_pct, min, max, mean, std }] }`. Для отсутствующей таблицы — `{ error: "table not found" }`. |
| `ColumnHistogram/` | `cmd.data.dataset.column_histogram` | Гистограмма одной численной колонки. Обрабатывается в `KafkaConsumerService.HandleColumnHistogramAsync` через `DatasetRepository.GetColumnHistogramAsync`. Параметры: `table`, `column`, `buckets` (clamp 2..500, default 30). Использует `width_bucket(col, lo, hi + (hi−lo)*1e-9, buckets)` (правая граница exclusive), GROUP BY bucket, clamp индекса 0..buckets−1, пустые buckets дозаполняются нулями. При hi ≤ lo — один bucket с полным count. Ответ: `{ column, min, max, buckets: [{ range_start, range_end, count }] }`. |
| `Browse/` | `cmd.data.dataset.browse` | Постраничный просмотр сырых строк таблицы. Обрабатывается в `KafkaConsumerService.HandleBrowseAsync` через `DatasetRepository.BrowseRowsAsync`. Параметры: `table` (обязательный), `page` (0-based, def. 0), `page_size` (1..500, def. 50), `order` (`"desc"` / `"asc"`, def. `"desc"`). `DateTime`/`DateTimeOffset` → ISO-8601, `decimal` → `ToString(InvariantCulture)`. Ответ: `{ table, page, page_size, total_rows, rows }`. При отсутствующей таблице — `{ total_rows: 0, rows: [] }`. |
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
| `cmd.data.dataset.export` | req/reply | Два режима по shape payload. **Single** `{ table, start_ms, end_ms }` → `{ presigned_url }` (потоковый `COPY TO STDOUT` → `System.IO.Pipelines` → MinIO multipart → 60-мин presigned URL, host переписан на `MINIO_PUBLIC_URL`). **Multi-table ZIP** `{ tables: string[], start_ms, end_ms }` → `{ claim_check }` (ZipArchive над MemoryStream, CSV каждой таблицы стримится в entry напрямую из Postgres; итог в MinIO через `PutBytesAsync`, `application/zip`). См. раздел «CSV export» в README. |
| `cmd.data.dataset.ingest` | req/reply | Bybit fetch → RSI-14 → upsert (см. README раздел «Ingest pipeline») |
| `cmd.data.dataset.delete_rows` | req/reply | Очистка таблицы (TRUNCATE при обоих `null`) или DELETE по `[start_ms, end_ms]`. Ответ: `{ status, table, rows_deleted }` |
| `cmd.data.dataset.column_stats` | req/reply | df.info()-стиль агрегаты по всем колонкам (Non-Null/Min/Max/Mean/Std + null_pct, каст `::float8`). Используется Anomaly Inspect. |
| `cmd.data.dataset.column_histogram` | req/reply | Гистограмма одной численной колонки (`width_bucket`, def. 30 buckets, clamp 2..500). Используется Anomaly Inspect при клике по строке таблицы. |
| `cmd.data.dataset.browse` | req/reply | Постраничный просмотр строк (`page` 0-based, `page_size` 1..500, `order` asc/desc). Ответ: `{ table, page, page_size, total_rows, rows }`. Используется Anomaly Browse. |
| `cmd.data.dataset.compute_features` | req/reply | Идемпотентный SQL-пересчёт 27 feature-колонок через window functions (`LAG`, `AVG/STDDEV_POP/MIN/MAX OVER PARTITION BY symbol, timeframe`). Перед `UPDATE` — `ALTER TABLE … ADD COLUMN IF NOT EXISTS` для каждой фичи (nullable `double precision`). Payload `{ table }` → `{ status, table, rows_updated }`. Автоматически вызывается в ingest как отдельный прогресс-этап `compute_features`. |
| `events.data.ingest.progress` | event (out) | Поэтапный прогресс ingest-а: `{ correlation_id, stage, label, status, progress, detail }` — потребляется frontend-ом через SSE |
| `cmd.data.dataset.normalize_timeframe` | req/reply | Нормализация строки таймфрейма |
| `cmd.data.dataset.make_table_name` | req/reply | Построение имени таблицы |
| `cmd.data.dataset.instrument_details` | req/reply | Метаданные инструмента |
| `cmd.data.dataset.table_schema` | req/reply | Схема колонок таблицы |
| `cmd.data.dataset.find_missing` | req/reply | Пропуски в данных |
| `cmd.data.dataset.timestamps` | req/reply | Список временных меток |
| `cmd.data.dataset.constants` | req/reply | Таймфреймы, дефолты |
| `events.data.dataset.updated` | event (out) | Данные обновлены после ingestion |
