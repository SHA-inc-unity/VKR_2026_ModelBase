# microservice_data

**Роль:** Единственный владелец рыночных данных (PostgreSQL). Предоставляет операции с датасетом остальным сервисам **исключительно через Kafka** (Redpanda). HTTP используется только для `GET /health` (Docker healthcheck) и одного публичного `GET /datasets` для отладки.

**Стек:** C#, .NET 8, ASP.NET Core, PostgreSQL 16, Kafka (aiokafka / Confluent), MinIO (S3 claim-check)  
**Порт:** `8100`  
**Зависимости:** `microservice_infra` должен быть запущен первым (создаёт `modelline_net`, Redpanda, MinIO)

## Kafka interface

| Topic                                  | Direction | Type      | Description                                          |
|----------------------------------------|-----------|-----------|------------------------------------------------------|
| `cmd.data.health`                      | in        | req/reply | Liveness + version                                   |
| `cmd.data.db.ping`                     | in        | req/reply | PostgreSQL connectivity check                        |
| `cmd.data.dataset.list_tables`         | in        | req/reply | List dataset tables                                  |
| `cmd.data.dataset.coverage`            | in        | req/reply | `{exists, rows, min_ts_ms, max_ts_ms}`               |
| `cmd.data.dataset.timestamps`          | in        | req/reply | All timestamps (ms) in `[start_ms, end_ms]`          |
| `cmd.data.dataset.find_missing`        | in        | req/reply | Missing timestamps (ms) for a stepped grid           |
| `cmd.data.dataset.rows`                | in        | req/reply | Row slice (projects `timestamp_ms`)                  |
| `cmd.data.dataset.export`              | in        | req/reply | CSV export via MinIO claim-check                     |
| `cmd.data.dataset.table_schema`        | in        | req/reply | Column names + types                                 |
| `cmd.data.dataset.normalize_timeframe` | in        | req/reply | Resolve timeframe alias                              |
| `cmd.data.dataset.make_table_name`     | in        | req/reply | Build canonical `{symbol}_{timeframe}`               |
| `cmd.data.dataset.instrument_details`  | in        | req/reply | Bybit instrument launch + first-funding timestamps   |
| `cmd.data.dataset.constants`           | in        | req/reply | Supported timeframes + page limits                   |
| `cmd.data.dataset.ingest`              | in        | req/reply | **Fetch Bybit → RSI → upsert** in the given window   |
| `cmd.data.dataset.delete_rows`         | in        | req/reply | Delete rows by range or TRUNCATE whole table         |
| `cmd.data.dataset.column_stats`        | in        | req/reply | df.info()-style per-column stats (non-null/min/max/mean/std) |
| `cmd.data.dataset.column_histogram`    | in        | req/reply | Distribution histogram for a single numeric column   |
| `events.data.ingest.progress`          | out       | event     | Staged ingest progress (fire-and-forget, no reply)   |

### Ingest pipeline (`cmd.data.dataset.ingest`)

Payload: `{ symbol, timeframe, start_ms, end_ms }`. The handler:

1. Resolves the timeframe (alias-aware) and the canonical table name
   `{symbol}_{timeframe}` in `crypt_date`.
2. Creates the target table if missing — schema:
   `timestamp_utc TIMESTAMPTZ PRIMARY KEY, symbol VARCHAR, exchange VARCHAR,
    timeframe VARCHAR, index_price NUMERIC, funding_rate NUMERIC,
    open_interest NUMERIC, rsi NUMERIC`.
3. Computes the set of **missing timestamps** via
   `generate_series(start, end, step) EXCEPT existing`. If the set is empty,
   returns early — no Bybit traffic at all.
4. Fetches the three Bybit feeds in parallel. Each feed is itself sliced into
   independent time-windows and fetched concurrently under
   `DatasetConstants.MaxParallelApiWorkers` — **no server cursors**, all three
   clients follow the same pattern:
   - **`/v5/market/index-price-kline`** over `[fetchStart, e]` (where
     `fetchStart = s − warmup*stepMs`, warmup covers RSI-14). Window size:
     `PageLimitKline × stepMs`.
   - **`/v5/market/funding/history`** over
     `[missingStart − fundingIntervalMs, missingEnd]` only — one 8h bucket
     back as forward-fill buffer. Window size:
     `PageLimitFunding × fundingIntervalMs` (default 8h).
   - **`/v5/market/open-interest`** at
     `ChooseOpenInterestInterval(stepMs)`, over
     `[missingStart − oiIntervalMs, missingEnd]` — one interval back as
     forward-fill buffer. Window size: `PageLimitOpenInterest × intervalMs`.
5. Forward-fills funding rate + OI onto candle timestamps.
6. Computes **Wilder's RSI (period 14)** over the warmup+main series
   (seeded with a simple average of the first 14 deltas, then recursive
   smoothing). The computation is **parallelised** across
   `clamp(Environment.ProcessorCount, 2, 8)` segments: a single cheap
   sequential pass captures the exact `(avgGain, avgLoss)` smoothing state
   at each segment boundary, then workers fan out with `Task.WhenAll`,
   producing values identical to the sequential algorithm.
7. Bulk-upserts only the rows whose timestamps are in the missing set via
   `INSERT ... SELECT * FROM UNNEST(@ts::timestamptz[], ...) ON CONFLICT
   (timestamp_utc) DO UPDATE SET ...`, batched by
   `DatasetConstants.UpsertBatchSize` (50 000).

Response: `{ status, table, rows_written, missing, fetched_klines,
fetched_funding, fetched_oi }`.

### Delete rows (`cmd.data.dataset.delete_rows`)

Payload: `{ table, start_ms?, end_ms? }`. Both timestamps are optional:

- When **both are omitted**, the target table is fully emptied via
  `TRUNCATE TABLE`. The reply's `rows_deleted` reports the row count observed
  before truncation.
- When **both are provided**, a `DELETE ... WHERE timestamp_utc IN
  [start_ms, end_ms]` is issued and the affected row count is returned.
- Missing table → `rows_deleted: 0` (no-op, no error).

Response: `{ status: "ok", table, rows_deleted }`.

### Ingest progress events (`events.data.ingest.progress`)

While the ingest command is running, the handler emits fire-and-forget
progress events on `events.data.ingest.progress` through
`KafkaProducer.PublishEventAsync` (no envelope — `correlation_id` is placed
directly inside the payload so downstream SSE consumers can correlate
without unwrapping). The payload shape is:

```json
{
  "correlation_id": "<hex>",
  "stage":   "prepare | fetch_klines | fetch_funding | fetch_oi | compute_rsi | upsert",
  "label":   "Подготовка таблицы | Загрузка свечей | …",
  "status":  "running | done | error",
  "progress": 0..100,
  "detail":  "free-form human-readable status, e.g. '12 / 200 страниц'"
}
```

Every stage emits `running` on entry and `done` on completion; on failure
the currently-active stage emits `status: error` before the command reply
propagates the error. `fetch_klines` additionally emits throttled
intermediate `running` updates (at most once every 10 completed pages +
a final event), driven by the `onPageDone` callback on
`BybitApiClient.FetchIndexPriceKlinesAsync`. `compute_rsi` emits one
`running` update per finished segment.

### Resilience

- **Старт без топиков.** Подписка выполняется в `ExecuteAsync` через `SubscribeWithRetryAsync`
  с экспоненциальным backoff (≤ 30 с), `AllowAutoCreateTopics=true` и
  `TopicMetadataRefreshIntervalMs=5000`. `SetErrorHandler` понижает уровень не-фатальных
  ошибок librdkafka («Subscribed topic not available», `LeaderNotAvailable`,
  `UnknownTopicOrPart`) до `Debug`, что устраняет шум при первом запуске Redpanda.
- **Транзиентные ошибки в `Consume()`** (`UnknownTopicOrPart`, `LeaderNotAvailable`,
  `Local_UnknownTopic`, `Local_UnknownPartition`, `NotCoordinatorForGroup`,
  `GroupLoadInProgress`) ловятся и переводятся в задержку 1 с — потребитель не падает.
- **Безопасный JSON-парсинг.** Все handlers (`HandleCoverageAsync`, `HandleTimestampsAsync`,
  `HandleRowsAsync`, …) используют `TryGetString` / `TryGetInt64`. При отсутствии
  обязательного поля клиенту возвращается `{ error: "missing fields: ..." }` вместо
  `KeyNotFoundException` — никаких 500-х в логах потребителя.

## Local run

```powershell
pip install -r requirements.txt
$env:KAFKA_BOOTSTRAP_SERVERS="localhost:9092"
python -m app.main
# HTTP health: http://localhost:8100/health (docker use only)
```

Prerequisite: `microservice_infra` running (Redpanda + MinIO).

## Roadmap

- ✅ **Step 1:** FastAPI skeleton + health/ready.
- ✅ **Step 1.5 (current):** Kafka integration, `cmd.data.health` handler.
- ⏳ **Step 2:** absorb `backend/dataset/*`, `backend/db.py`, `backend/csv_io.py`;
  publish dataset commands over Kafka; large payloads via MinIO claim-check.
- ⏳ **Step 3:** Binance API client + scheduler for background ingestion.
