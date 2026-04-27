# microservice_data — Структура

> Обновляй этот файл при каждом изменении модулей, классов или ключевых функций.

---

## Dataset jobs (редизайн «длительные операции — это jobs»)

Все длительные операции датасета (ingest, detect_anomalies, compute_features,
clean_apply, export, import_csv, upsert_ohlcv) принимаются как **jobs** —
синхронные RPC-команды только инициируют запись в `dataset_jobs` и сразу
возвращают `{ job_id, status: 'queued', conflict?: { existing_job_id } }`.
Реальная работа идёт в `DatasetJobRunner` (BackgroundService), а прогресс
публикуется в `events.data.dataset.job.progress` / `…job.completed`.

Контракт топиков:

- `cmd.data.dataset.jobs.start` — создать job (валидация payload, вставка
  в `queued`; если `(target_table, conflict_class)` уже занят — возвращает
  `conflict_existing_job_id`).
- `cmd.data.dataset.jobs.cancel` — выставить `cancel_requested=true`;
  бегущая job ловит CancellationToken на следующем `await` и завершается
  как `canceled`.
- `cmd.data.dataset.jobs.get` / `cmd.data.dataset.jobs.list` — read-only
  view (`active` | `terminal` | конкретный статус), включая `stages[]` и
  `subtasks[]`.

Семантика статусов: `queued | running | succeeded | failed | canceled |
skipped` (`skipped` — когда работы нет, например, нет недостающих свечей).

Ключевые компоненты:

| Файл | Роль |
|------|------|
| `Jobs/DatasetJobRunner.cs` | BackgroundService-шедулер. На старте вызывает `ReclaimOrphansAsync` (`running → failed` с `error_code='service_restart'`), затем в цикле выбирает `queued`-задания пачками по 50, проверяет per-type `SemaphoreSlim` + `JobLockManager`, делает атомарный CAS `TryAcquireRunningAsync(version)` и диспатчит handler через `Task.Run`. Heartbeat пишется каждые 5 с. По завершении публикуется `events.data.dataset.job.completed` и освобождаются lock + slot. |
| `Jobs/JobLockManager.cs` | Process-local карта `(conflict_class, target_table) → job_id`. `mutating_table` блокирует запись по таблице, `read_heavy` ограничивается только лимитом на тип, `external_io` (ingest) лимитируется semaphore + Bybit rate limiter. |
| `Jobs/IDatasetJobHandler.cs` | Контракт handler-а + `JobContext` (репортер прогресса, проверка отмены, helpers `StartStageAsync` / `EndStageAsync` / `AddSubtaskAsync` / `UpdateSubtaskAsync`). Per-каждый прогресс публикуется в `events.data.dataset.job.progress`. |
| `Jobs/JobHandlers.cs` | 7 реализаций `IDatasetJobHandler`. `IngestJobHandler` пишет stages `prepare → fetch (klines/funding/oi parallel + RSI inline) → upsert → compute_features`. `CleanApplyJobHandler` открывает один `NpgsqlConnection` и применяет op-список (`drop_duplicates`, `fix_ohlc`, `fill_zero_streaks`). `DetectAnomaliesJobHandler` пробегает 10 detection stages. `ComputeFeaturesJobHandler` — обёртка над репозиторием. `Export/ImportCsv/UpsertOhlcv` — заглушки; legacy команды по-прежнему доступны. |
| `Database/DatasetJobsRepository.cs` | Read-only выборки + DDL `dataset_jobs` (со столбцами `progress`, `stage`, `target_table`, `conflict_class`, `cancel_requested`, `version`, `custom_metrics jsonb`). Метод `MapJobPublic` экспонирует маппер для мутатора. |
| `Database/DatasetJobsMutator.cs` | Все мутации: `TryAcquireRunningAsync` (CAS по `version`), `UpdateProgressAsync`, `HeartbeatAsync`, `FinishAsync`, `PickQueuedAsync`, `IsCancelRequestedAsync`, `ReclaimOrphansAsync`, `Add/UpdateSubtaskAsync`, `Start/EndStageAsync` (последний апсертит `custom_metrics`). |
| `Bybit/BybitRateLimiter.cs` | Process-local token-bucket (default 96 r/s = 80 % от Bybit IP-лимита) + `BybitApiException(retCode, retMsg)`. `BybitApiClient.GetJsonAsync` ждёт токен, делает экспоненциальный backoff с jitter на 429/5xx, ловит `retCode != 0` и пробрасывает без retry. |
| `Database/DatasetRepository.cs` | Метод `GetCoverageRangeAsync(table, startMs, endMs, stepMs)` — coverage внутри окна (rows-in-range + expected через `generate_series`). Используется в `HandleCoverageAsync`, когда передан явный `[start_ms, end_ms]`. |

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
| `DatasetRepository.cs` | `DatasetRepository`, `DatasetRepository.MarketRow` | CRUD для таблиц `{symbol}_{timeframe}`. Схема сырых данных (13 колонок): `timestamp_utc TIMESTAMPTZ PK, symbol, exchange, timeframe, close_price, open_price, high_price, low_price, volume, turnover, funding_rate, open_interest, rsi`. `MarketRow` — record из 13 полей. `BulkUpsertAsync` — UNNEST + `ON CONFLICT (timestamp_utc) DO UPDATE`, 13 параметров, батчи по `DatasetConstants.UpsertBatchSize`. `BulkUpdateOhlcvAsync(table, symbol, exchange, timeframe, rows)` — точечный UPSERT только пяти OHLCV-raw колонок (open/high/low/volume/turnover) через UNNEST; на конфликте `ON CONFLICT (timestamp_utc) DO UPDATE` обновляются **только** OHLCV-ячейки, остальные колонки (close_price, funding_rate, open_interest, rsi, derived features) сохраняются. `OhlcvRow(TimestampMs, Open, High, Low, Volume, Turnover)` — record. `ComputeAndUpdateFeaturesAsync` — двухуровнев...ый CTE (tr_cte для ATR) + `UPDATE` 37 feature-колонок (вкл. OHLCV-признаки). `GetColumnStatsAsync`, `GetColumnHistogramAsync` — Anomaly Inspect API. | Методы: `CreateTableIfNotExistsAsync` — `CREATE TABLE IF NOT EXISTS` + **schema migration**: затем итерирует по `RawTableSchema` (исключая `timestamp_utc`) и `FeatureTableSchema` и выполняет `ALTER TABLE "..." ADD COLUMN IF NOT EXISTS "..." <type>` для каждой колонки. Это позволяет старым таблицам (без OHLCV/turnover/feature-колонок) получить недостающие столбцы при следующем ingest без потери данных (идемпотентно). `GetCoverageAsync` / `GetCoverageIfExistsAsync` (`rows`, `min_ts_ms`, `max_ts_ms`), `FetchTimestampsAsync`, `FindMissingTimestampsAsync` (через `generate_series`), `FetchRowsAsync`, `ExportCsvToStreamAsync(table, startMs, endMs, Stream output, ct)` — **streaming CSV**: `NpgsqlConnection.BeginTextExport` с `COPY (SELECT <cols> , (EXTRACT(EPOCH FROM timestamp_utc)*1000)::bigint AS timestamp_ms FROM ... WHERE ... ORDER BY timestamp_utc) TO STDOUT WITH CSV HEADER`; возвращаемый `TextReader` переливается в `output` через `StreamWriter(UTF8, 64 KB)` кусками по ~32 K символов — нет `List<dynamic>`, нет `StringBuilder`, нет промежуточного `byte[]`. Имя колонки `timestamp_utc` в SELECT заменяется на вычисляемое `timestamp_ms` (контракт Kafka), идентификатор таблицы проходит `Safe()`. Используется `HandleExportAsync` в связке с `System.IO.Pipelines`. `BulkUpsertAsync` (UNNEST + `ON CONFLICT (timestamp_utc) DO UPDATE`, батчи по `DatasetConstants.UpsertBatchSize`), `DeleteRowsAsync(table, startMs?, endMs?)` — если оба timestamp-а `null` → `TRUNCATE TABLE` (возвращает число строк до очистки), иначе `DELETE WHERE timestamp_utc IN [start, end]`; для отсутствующей таблицы возвращает `0` без исключения. **Anomaly Inspect:** `GetColumnStatsAsync(table, columnFilter?, countOnly?)` — возвращает `ColumnStatsResult(TotalRows, Columns[ColumnStat(Name, Dtype, NonNull, Min, Max, Mean, Std)])` одним SQL-запросом; `columnFilter` (список имён) ограничивает набор колонок; `countOnly=true` убирает `MIN/MAX/AVG/STDDEV` — только `COUNT(*)` + per-column `COUNT("col")::bigint AS nn_{col}`; имена идентификаторов проходят `Safe()`-гард. `GetColumnHistogramAsync(table, column, buckets)` — возвращает `HistogramResult(Column, Min, Max, Buckets[HistogramBucket(RangeStart, RangeEnd, Count)])`; buckets clamp 2..500; `width_bucket(col, lo, hi + (hi-lo)*1e-9, buckets)` с обрезкой индекса 0..buckets-1; пустые buckets дозаполняются нулями; при hi ≤ lo — один bucket с полным count; для полностью null-колонки — пустой массив buckets. Для отсутствующей таблицы оба метода возвращают `null`. |
| `DatasetRepository.Anomaly.cs` | `AnomalyRow`, методы `Detect*`, `Apply*`, `Count*`, `Acquire/ReleaseApplyLockAsync`, `Ensure/WriteAuditLogAsync` | Partial-расширение `DatasetRepository`. **Detection** — 6 методов, выполняемых параллельно через `Task.WhenAll`: `DetectGapsAsync` (skips when `step_ms == 0`), `DetectDuplicatesAsync`, `DetectOhlcViolationsAsync` (сначала `GetColumnNamesAsync` → возвращает `[]` если отсутствует хотя бы один из `_ohlcCols={open_price,high_price,low_price,close_price}` — legacy-таблицы), `DetectNegativesAsync` (по `_negativeCols={open_price,high_price,low_price,close_price,volume,turnover,open_interest}`), `DetectZeroStreaksAsync` (gaps-and-islands ROW_NUMBER, `min_len=3` по `_streakCols={open_interest,funding_rate}`), `DetectStatisticalOutliersAsync` (single CTE `PERCENTILE_CONT` + `STDDEV_SAMP`, `z=3.0`, по `_outlierCols={close_price,volume,turnover,open_interest}`). Каждый метод возвращает `IReadOnlyList<AnomalyRow(TsMs, AnomalyType, Severity, Column, Value, Details)>`. **Apply**: `ApplyDropDuplicatesAsync` (DELETE USING ROW_NUMBER on ctid keep first), `ApplyFixOhlcAsync` (сначала проверяет `information_schema.columns` через открытый `conn`, возвращает `0` если отсутствует хотя бы один из `_ohlcCols`; иначе UPDATE high/low = GREATEST/LEAST), `ApplyFillZeroStreakAsync(table, column, conn)` (CTE running SUM grp + FIRST_VALUE forward fill UPDATE), `ApplyDeleteByTimestampsAsync` (NpgsqlParameter Array TimestampTz), `ApplyFillGapsAsync` (`generate_series` + LATERAL prv/nxt + per-column expressions: `linear` → `(prv)."col" + ((nxt)."col"-(prv)."col")*frac`; `forward_fill` → `COALESCE((prv)."col",(nxt)."col")`; скобки вокруг `prv`/`nxt` **обязательны** — без них PostgreSQL интерпретирует их как псевдонимы таблиц и падает с "missing FROM-clause entry"; ON CONFLICT (timestamp_utc) DO NOTHING). **Lock**: `AcquireApplyLockAsync` возвращает live `NpgsqlConnection` под `pg_advisory_lock(hash_record_extended(ROW(@t), 0))`; `ReleaseApplyLockAsync` снимает блокировку и dispose-ит соединение (вызывается в `finally`). **Audit**: `EnsureAuditLogAsync` создаёт `dataset_audit_log(id SERIAL PK, table_name, operation, params JSONB, rows_affected INT, applied_at TIMESTAMPTZ DEFAULT now())` + index. `WriteAuditLogAsync(table, op, paramsJson, rowsAffected)` → `int audit_id`. |

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
| `KafkaConsumerService.cs` | `KafkaConsumerService` (IHostedService) | Фоновый потребитель Kafka. Маршрутизирует `cmd.data.*` топики к use-case обработчикам. Устойчив к старту (resilient `SubscribeWithRetryAsync` с экспоненциальным backoff, `AllowAutoCreateTopics=true`, `SetErrorHandler` приглушает не-фатальные `UnknownTopicOrPart`/`LeaderNotAvailable`). Все обработчики используют безопасные аксессоры `TryGetString`/`TryGetInt64` вместо `GetProperty().GetString()` — отсутствующие поля возвращаются клиенту как `{ error: "missing fields: ..." }`, без `KeyNotFoundException`. **Two-tier concurrency**: `_concurrency = SemaphoreSlim(32)` гейтит все in-flight handlers; `_heavyConcurrency = SemaphoreSlim(4)` дополнительно ограничивает heavy-операции (`HeavyTopics`: `export`, `ingest`, `detect_anomalies`, `clean.preview`, `clean.apply`, `compute_features`, `import_csv`, `upsert_ohlcv`, `column_stats`, `column_histogram`). Лёгкие команды (health/db.ping/list_tables/coverage/timestamps/find_missing/rows/browse/table_schema/audit_log/delete_rows) проходят без heavy-слота — burst тяжёлых запросов не исчерпывает PostgreSQL-пул. |
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
| `ExportDataset/` | `cmd.data.dataset.export` | Два режима экспорта по shape payload-а, оба возвращают `{ presigned_url }`. **Single-table** (`{ table, start_ms, end_ms }`): потоковый pipeline — `KafkaConsumerService.HandleExportAsync` открывает `System.IO.Pipelines.Pipe`, параллельно запускает два `Task.Run`: writer → `DatasetRepository.ExportCsvToStreamAsync` (PostgreSQL `COPY (...) TO STDOUT WITH CSV HEADER` через `BeginTextExport` → `StreamWriter` в `pipe.Writer.AsStream(leaveOpen:true)`), reader → `MinioClaimCheckService.PutStreamAsync` из `pipe.Reader.AsStream(leaveOpen:true)` (multipart upload). Стороны pipe закрываются явно через `CompleteAsync(captured)` — ошибка не теряется при dispose стрима. Ключ объекта — `exports/{guid}.csv`. После `Task.WhenAll` вызывается `MinioClaimCheckService.GetPresignedUrlAsync(key, MinioPublicUrl, 60, "<table>.csv", "text/csv; charset=utf-8")`; ответ: `{ presigned_url }`. Память: ~64 KB буфер pipe + 5 MB multipart part. **Multi-table ZIP** (`{ tables: string[], symbol: string, start_ms, end_ms }`): тот же Pipe-паттерн — producer открывает `ZipArchive(pipe.Writer.AsStream(leaveOpen:true), ZipArchiveMode.Create, leaveOpen:true)`, последовательно (не параллельно) итерирует таблицы, каждый entry открывает через `archive.CreateEntry("{table}.csv", CompressionLevel.Fastest)` → `entry.Open()` → `_repo.ExportCsvToStreamAsync(...)` прямо в entry stream. После цикла `archive.Dispose()` сбрасывает central directory в pipe writer stream. Consumer — `PutStreamAsync(pipe.Reader.AsStream(), zipKey, "application/zip", ct)`. После `Task.WhenAll` вызывается `GetPresignedUrlAsync(zipKey, ..., downloadFilename="{symbol}_ALL.zip", contentType="application/zip")`; ответ: `{ presigned_url }`. Память: ~64 KB буфер pipe + 5 MB multipart part. |
| `IngestData/` | `cmd.data.dataset.ingest` | Полный pipeline: Bybit (klines + funding + OI параллельно) → forward-fill → Wilder RSI-14 (параллельно, `clamp(ProcessorCount,2,8)` сегментов с seed-ом состояния) → UNNEST-upsert только по отсутствующим timestamps. **Incremental fetch range**: klines грузятся на `[fetchStart, e]` (RSI warmup), но OI и funding — только на `[missingStart − intervalMs, missingEnd]`, где `missingStart/missingEnd` = мин/макс из `FindMissingTimestampsAsync`, а `intervalMs` берётся из `ChooseOpenInterestInterval(stepMs).IntervalMs` для OI и константного `28_800_000` (8 ч) для funding — это убирает лишние сотни параллельных запросов в Bybit. По ходу handler публикует события `events.data.ingest.progress` по 6 стадиям (`prepare`, `fetch_klines`, `fetch_funding`, `fetch_oi`, `compute_rsi`, `upsert`) со статусами `running` / `done` / `error` и полем `progress` 0..100 |
| `DeleteRows/` | `cmd.data.dataset.delete_rows` | Удаление строк по диапазону (`start_ms`, `end_ms` — оба обязательны) или полная очистка таблицы (оба null → `TRUNCATE`). Обрабатывается в `KafkaConsumerService.HandleDeleteRowsAsync` напрямую через `DatasetRepository.DeleteRowsAsync`. Ответ: `{ status, table, rows_deleted }` |
| `ColumnStats/` | `cmd.data.dataset.column_stats` | df.info()-style агрегаты по колонкам таблицы. Обрабатывается в `KafkaConsumerService.HandleColumnStatsAsync` через `DatasetRepository.GetColumnStatsAsync`. **Необязательные поля payload:** `columns` (string[]) — ограничить запрос конкретными колонками (остальные пропускаются); `count_only` (bool) — вычислять только `COUNT(*) + COUNT(col)`, без `MIN/MAX/AVG/STDDEV` (многократно быстрее на больших таблицах). Один динамический SQL: `COUNT(*)` + per-column `COUNT("col")::bigint AS nn_{col}` + для численных типов (`numeric / double precision / real / integer / bigint / smallint`) — опционально `MIN/MAX/AVG/STDDEV_POP ::float8 AS min_/max_/avg_/std_{col}` (каст в `double precision`, чтобы избежать `System.Decimal` overflow). Ответ: `{ table, total_rows, columns: [{ name, dtype, non_null, null_count, null_pct, min, max, mean, std }] }`. Для отсутствующей таблицы — `{ error: "table not found" }`. |
| `ColumnHistogram/` | `cmd.data.dataset.column_histogram` | Гистограмма одной численной колонки. Обрабатывается в `KafkaConsumerService.HandleColumnHistogramAsync` через `DatasetRepository.GetColumnHistogramAsync`. Параметры: `table`, `column`, `buckets` (clamp 2..500, default 30). Использует `width_bucket(col, lo, hi + (hi−lo)*1e-9, buckets)` (правая граница exclusive), GROUP BY bucket, clamp индекса 0..buckets−1, пустые buckets дозаполняются нулями. При hi ≤ lo — один bucket с полным count. Ответ: `{ column, min, max, buckets: [{ range_start, range_end, count }] }`. |
| `Browse/` | `cmd.data.dataset.browse` | Постраничный просмотр сырых строк таблицы. Обрабатывается в `KafkaConsumerService.HandleBrowseAsync` через `DatasetRepository.BrowseRowsAsync`. Параметры: `table` (обязательный), `page` (0-based, def. 0), `page_size` (1..500, def. 50), `order` (`"desc"` / `"asc"`, def. `"desc"`). `DateTime`/`DateTimeOffset` → ISO-8601, `decimal` → `ToString(InvariantCulture)`. Ответ: `{ table, page, page_size, total_rows, rows }`. При отсутствующей таблице — `{ total_rows: 0, rows: [] }`. |
| `GetTableSchema/` | `cmd.data.dataset.table_schema` | Схема таблицы (колонки, типы) |
| `GetTimestamps/` | `cmd.data.dataset.timestamps` | Список временных меток |
| `FindMissing/` | `cmd.data.dataset.find_missing` | Поиск пропусков в данных |
| `GetInstrument/` | `cmd.data.dataset.instrument_details` | Метаданные инструмента |
| `UpsertOhlcv/` (logical) | `cmd.data.dataset.upsert_ohlcv` | Точечный апдейт **только** пяти OHLCV-сырых колонок (open/high/low/volume/turnover) для уже существующего датасета. Обрабатывается в `KafkaConsumerService.HandleUpsertOhlcvAsync` через `DatasetRepository.BulkUpdateOhlcvAsync`. Полезен для сценария «дозалить пропавшие свечи без перезаписи RSI / funding / open_interest / derived features». Парсинг строк через `TryGetInt64("ts_ms")` + `TryGetDecimal` (Number/String/Null, InvariantCulture). Перед upsert вызывается `CreateTableIfNotExistsAsync`. Ответ: `{ rows_affected }`. |

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
| `cmd.data.dataset.export` | req/reply | Два режима по shape payload, оба возвращают `{ presigned_url }`. **Single** `{ table, start_ms, end_ms }` → потоковый `COPY TO STDOUT` → `System.IO.Pipelines` → MinIO multipart → 60-мин presigned URL. **Multi-table ZIP** `{ tables: string[], symbol: string, start_ms, end_ms }` → тот же Pipe-паттерн, producer использует `ZipArchive` над `pipe.Writer.AsStream()`, последовательный цикл по таблицам с `CompressionLevel.Fastest`; consumer — `PutStreamAsync`; итог — presigned URL с `downloadFilename="{symbol}_ALL.zip"`. |
| `cmd.data.dataset.ingest` | req/reply | Bybit fetch → RSI-14 → upsert (см. README раздел «Ingest pipeline») |
| `cmd.data.dataset.delete_rows` | req/reply | Очистка таблицы (TRUNCATE при обоих `null`) или DELETE по `[start_ms, end_ms]`. Ответ: `{ status, table, rows_deleted }` |
| `cmd.data.dataset.column_stats` | req/reply | df.info()-стиль агрегаты по всем колонкам (Non-Null/Min/Max/Mean/Std + null_pct, каст `::float8`). Используется Anomaly Inspect. |
| `cmd.data.dataset.column_histogram` | req/reply | Гистограмма одной численной колонки (`width_bucket`, def. 30 buckets, clamp 2..500). Используется Anomaly Inspect при клике по строке таблицы. |
| `cmd.data.dataset.browse` | req/reply | Постраничный просмотр строк (`page` 0-based, `page_size` 1..500, `order` asc/desc). Ответ: `{ table, page, page_size, total_rows, rows }`. Используется Anomaly Browse. |
| `cmd.data.dataset.detect_anomalies` | req/reply | Детектор аномалий: gaps (`generate_series` по `step_ms`), duplicates (`GROUP BY HAVING COUNT>1`), OHLC violations, negatives, zero-streaks, statistical outliers (`z=3.0`), rolling z-score/IQR, stale price, return outliers, volume mismatch. **Summary-first ответ**: `{ table, total, critical, warning, by_type, sample[≤200], page, page_size, has_more, rows?, report_url? }`. `sample` — top critical-first срез (≤200 строк) для быстрого отображения. Постраничная навигация по полному списку — через `{ page, page_size }` (page_size clamp 1..5000) → `rows` несёт slice. Когда `total > 200`, полный отчёт (все строки) сохраняется в MinIO `reports/anomaly_<guid>.json` и presigned URL возвращается в `report_url` (60 мин TTL). UI никогда не получает unbounded JSON-массив. |
| `cmd.data.dataset.clean.preview` | req/reply | Превью операций очистки. Ответ: `{ table, counts: { drop_duplicates, fix_ohlc, fill_zero_streaks, delete_by_timestamps, fill_gaps } }`. |
| `cmd.data.dataset.clean.apply` | req/reply | Применение очистки в БД. Требует `confirm: true`. `pg_advisory_lock(hash_record_extended(ROW(@t), 0))` ключ по имени таблицы. Порядок: drop_duplicates → fix_ohlc → fill_zero_streaks (loop columns с `try/except PostgresException` для отсутствующих) → delete_by_timestamps → fill_gaps (`linear` или `forward_fill`). Аудит: `EnsureAuditLogAsync` создаёт `dataset_audit_log(id SERIAL PK, table_name, operation, params JSONB, rows_affected INT, applied_at TIMESTAMPTZ)`; `WriteAuditLogAsync` возвращает `audit_id`. **Lock leak fix**: `WriteAuditLogAsync` открывает собственное соединение (не переиспользует `conn` из `AcquireApplyLockAsync`), поэтому пара audit + `ReleaseApplyLockAsync` обёрнута в `try/finally` — advisory-lock гарантированно снимается даже если INSERT в `dataset_audit_log` бросит исключение. Ответ: `{ table, audit_id, rows_affected: {…}, total }`. |
| `cmd.data.dataset.compute_features` | req/reply | Идемпотентный SQL-пересчёт 27 feature-колонок через window functions (`LAG`, `AVG/STDDEV_POP/MIN/MAX OVER PARTITION BY symbol, timeframe`). Перед `UPDATE` — `ALTER TABLE … ADD COLUMN IF NOT EXISTS` для каждой фичи (nullable `double precision`). Обе SQL-команды (ALTER и UPDATE) выполняются с `commandTimeout: 0` (бесконечный Npgsql-таймаут) — для 1m-таблиц с >2.5 млн строк дефолтных 30 с недостаточно (остальные запросы репозитория сохраняют дефолтный таймаут). Payload `{ table }` → `{ status, table, rows_updated }`. Автоматически вызывается в ingest как отдельный прогресс-этап `compute_features`. |
| `events.data.ingest.progress` | event (out) | Поэтапный прогресс ingest-а: `{ correlation_id, stage, label, status, progress, detail }` — потребляется frontend-ом через SSE |
| `cmd.data.dataset.normalize_timeframe` | req/reply | Нормализация строки таймфрейма |
| `cmd.data.dataset.make_table_name` | req/reply | Построение имени таблицы |
| `cmd.data.dataset.instrument_details` | req/reply | Метаданные инструмента |
| `cmd.data.dataset.table_schema` | req/reply | Схема колонок таблицы |
| `cmd.data.dataset.find_missing` | req/reply | Пропуски в данных |
| `cmd.data.dataset.timestamps` | req/reply | Список временных меток |
| `cmd.data.dataset.constants` | req/reply | Таймфреймы, дефолты |
| `events.data.dataset.updated` | event (out) | Данные обновлены после ingestion |
