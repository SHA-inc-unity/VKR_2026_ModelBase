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
| `cmd.data.dataset.export`              | in        | req/reply | CSV export: streaming per-table (→ presigned URL) or bundled multi-table ZIP (→ claim-check) |
| `cmd.data.dataset.table_schema`        | in        | req/reply | Column names + types                                 |
| `cmd.data.dataset.normalize_timeframe` | in        | req/reply | Resolve timeframe alias                              |
| `cmd.data.dataset.make_table_name`     | in        | req/reply | Build canonical `{symbol}_{timeframe}`               |
| `cmd.data.dataset.instrument_details`  | in        | req/reply | Bybit instrument launch + first-funding timestamps   |
| `cmd.data.dataset.constants`           | in        | req/reply | Supported timeframes + page limits                   |
| `cmd.data.dataset.ingest`              | in        | req/reply | **Fetch Bybit → RSI → upsert** in the given window   |
| `cmd.data.dataset.delete_rows`         | in        | req/reply | Delete rows by range or TRUNCATE whole table         |
| `cmd.data.dataset.column_stats`        | in        | req/reply | df.info()-style per-column stats (non-null/min/max/mean/std) — values cast to `float8` to avoid Decimal overflow |
| `cmd.data.dataset.column_histogram`    | in        | req/reply | Distribution histogram for a single numeric column   |
| `cmd.data.dataset.browse`             | in        | req/reply | Paginated raw-row browse (`page`, `page_size` 1–500, `order` asc/desc). Returns `{ table, page, page_size, total_rows, rows[] }` |
| `cmd.data.dataset.compute_features`    | in        | req/reply | Idempotent SQL pass: `ALTER TABLE … ADD COLUMN IF NOT EXISTS` + `UPDATE` via window functions over raw OHLC/OI/RSI → 27 feature columns. Payload `{ table }` → `{ status, table, rows_updated }` |
| `events.data.ingest.progress`          | out       | event     | Staged ingest progress (fire-and-forget, no reply)   |

### Ingest pipeline (`cmd.data.dataset.ingest`)

Payload: `{ symbol, timeframe, start_ms, end_ms }`. The handler:

1. Resolves the timeframe (alias-aware) and the canonical table name
   `{symbol}_{timeframe}` in `crypt_date`.
2. Creates the target table if missing — schema:
   `timestamp_utc TIMESTAMPTZ PRIMARY KEY, symbol VARCHAR, exchange VARCHAR,
    timeframe VARCHAR, index_price NUMERIC, open_price NUMERIC, high_price NUMERIC,
    low_price NUMERIC, volume NUMERIC, turnover NUMERIC,
    funding_rate NUMERIC, open_interest NUMERIC, rsi NUMERIC`.
3. Computes the set of **missing timestamps** via
   `generate_series(start, end, step) EXCEPT existing`. If the set is empty,
   returns early — no Bybit traffic at all.
4. Fetches the three Bybit feeds in parallel. Each feed is itself sliced into
   independent time-windows and fetched concurrently under
   `DatasetConstants.MaxParallelApiWorkers` — **no server cursors**, all three
   clients follow the same pattern:
   - **`/v5/market/kline`** over `[fetchStart, e]` (where
     `fetchStart = s − warmup*stepMs`, warmup covers RSI-14). Returns 7 fields:
     `[startMs, open, high, low, close, volume, turnover]`.
     Window size: `PageLimitKline × stepMs`.
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
8. **`compute_features`** stage — публикуется отдельным
   `events.data.ingest.progress` и выполняется через
   `DatasetRepository.ComputeAndUpdateFeaturesAsync`: идемпотентный
   `ALTER TABLE … ADD COLUMN IF NOT EXISTS` для 37 feature-колонок
   (`double precision`, nullable), затем единый `UPDATE` из CTE поверх
   PostgreSQL window-функций (`LAG`, `AVG/STDDEV_POP/MIN/MAX OVER
   (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS …)`).
   ATR вычисляется через двухуровневый CTE (сначала TR, затем rolling AVG).

Response: `{ status, table, rows_written, missing, fetched_klines,
fetched_funding, fetched_oi, features_updated, features_error }`.

### CSV export (`cmd.data.dataset.export`)

Two payload shapes, dispatched on the presence of `tables`:

**Single table** — `{ table, start_ms, end_ms }` → `{ presigned_url }`
(full streaming, no intermediate buffers).

**Multi-table ZIP** — `{ tables: string[], start_ms, end_ms }` →
`{ claim_check: { key, bucket, size, url } }`. The handler opens a
`ZipArchive(MemoryStream, ZipArchiveMode.Create, leaveOpen:true)`, and
for each requested table creates a `CompressionLevel.Optimal` entry
`{table}.csv`; `entry.Open()` exposes a writable stream into which
`DatasetRepository.ExportCsvToStreamAsync` streams CSV directly from
PostgreSQL (`COPY TO STDOUT`). Individual CSVs are never materialised
as `byte[]` — only the final compressed ZIP is buffered (compression
ratio makes this cheap vs. raw text). The ZIP is uploaded to MinIO
under `exports/{guid}.zip` via `MinioClaimCheckService.PutBytesAsync`
and a claim-check descriptor is returned. This mode exists because
Chromium-based browsers suppress programmatic multi-file download
loops after the first few `<a>.click()` calls — delivering one archive
sidesteps the limit entirely.

### Streaming CSV export (single table)

Payload: `{ table, start_ms, end_ms }`. Replies with `{ presigned_url }`
(string). The whole path is streaming — no intermediate buffer anywhere:

1. **PostgreSQL side.** `DatasetRepository.ExportCsvToStreamAsync` opens a
   `COPY (SELECT <cols-without-timestamp_utc>, (EXTRACT(EPOCH FROM
   timestamp_utc)*1000)::bigint AS timestamp_ms FROM "<tbl>" WHERE
   timestamp_utc BETWEEN $s AND $e ORDER BY timestamp_utc) TO STDOUT WITH
   CSV HEADER` via `NpgsqlConnection.BeginTextExport` — PostgreSQL
   generates CSV itself and streams rows back through the Npgsql
   `TextReader`. No Dapper `List<dynamic>`, no `StringBuilder`, no
   `GetBytes()`. The reader is drained into the output stream in ~32 K
   char chunks via a `StreamWriter` (`UTF8Encoding`, 64 KB buffer).
2. **Pipe bridge.** `KafkaConsumerService.HandleExportAsync` creates a
   `System.IO.Pipelines.Pipe`. One `Task.Run` feeds `ExportCsvToStreamAsync`
   into `pipe.Writer.AsStream(leaveOpen:true)`; another feeds
   `pipe.Reader.AsStream(leaveOpen:true)` into
   `MinioClaimCheckService.PutStreamAsync`. Pipe sides are completed
   explicitly (with or without the captured exception) so errors survive
   stream disposal instead of being swallowed as clean EOF.
3. **MinIO side.** `PutStreamAsync` uses AWSSDK.S3
   `TransferUtility.UploadAsync` with
   `TransferUtilityUploadRequest { AutoResetStreamPosition=false,
   AutoCloseStream=false, PartSize=5 MB }` — multipart upload as bytes
   arrive, no content-length required.
4. **Presigned URL.** `GetPresignedUrlAsync` signs a 60-minute GET URL
   (AWSSDK `GetPreSignedUrlRequest`) with overrides for
   `ResponseContentDisposition: attachment; filename="<table>.csv"` and
   `ResponseContentType: text/csv`. The SDK signs against the internal
   `Minio.Endpoint` (`http://minio:9000`) which browsers can't resolve,
   so the URL host is rewritten to `Minio.PublicUrl`
   (`MINIO_PUBLIC_URL` env var, default `http://localhost:9000`). The
   signature remains valid because MinIO doesn't bind SigV4 to the
   `Host` header.

MinIO objects live under `exports/{guid}.csv` and expire implicitly
after 60 minutes (the presigned URL's TTL — no manual cleanup needed;
the object itself can be life-cycled by a bucket policy if desired).
Peak RAM: pipe buffer (~64 KB) + one multipart part (~5 MB) — instead
of the prior ≈10 GB blow-up for a 1m/5y window.

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
`BybitApiClient.FetchKlinesAsync`. `compute_rsi` emits one
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

---

## Схема базы данных PostgreSQL

**БД:** `crypt_date` · **Хост контейнера:** `microservice_data-postgres-1`

### Таблицы

Одна таблица на комбинацию `{symbol}_{timeframe}`. Пример: `btcusdt_5m`, `btcusdt_1d`.

Поддерживаемые таймфреймы: `1m`, `3m`, `5m`, `15m`, `30m`, `60m`, `120m`, `240m`, `360m`, `720m`, `1d`.

### Сырые колонки (13 штук — источник: Bybit REST API)

| Колонка | Тип | Описание |
|---------|-----|----------|
| `timestamp_utc` | `TIMESTAMPTZ PK` | Время открытия свечи (UTC) |
| `symbol` | `VARCHAR` | Торговая пара, напр. `BTCUSDT` |
| `exchange` | `VARCHAR` | Биржа-источник, напр. `bybit` |
| `timeframe` | `VARCHAR` | Таймфрейм, напр. `5m` |
| `index_price` | `NUMERIC` | Цена закрытия торговой свечи (`/v5/market/kline`, `item[4]`) |
| `open_price` | `NUMERIC` | Цена открытия свечи (`item[1]`) |
| `high_price` | `NUMERIC` | Максимум свечи (`item[2]`) |
| `low_price` | `NUMERIC` | Минимум свечи (`item[3]`) |
| `volume` | `NUMERIC` | Объём в базовой монете (`item[5]`) |
| `turnover` | `NUMERIC` | Объём в котируемой монете — оборот (`item[6]`) |
| `funding_rate` | `NUMERIC` | Ставка фандинга на момент свечи (`/v5/market/funding/history`) |
| `open_interest` | `NUMERIC` | Открытый интерес в базовой монете (`/v5/market/open-interest`) |
| `rsi` | `NUMERIC` | RSI-14, вычислен на стороне сервиса (алгоритм Уайлдера) |

> **Примечание:** `index_price` хранит цену закрытия торговой свечи (`/v5/market/kline`). Полный OHLCV собирается с того же эндпоинта.

### Feature-колонки (37 штук — вычисляются SQL window-функциями)

Вычисляются командой `cmd.data.dataset.compute_features`. Тип `DOUBLE PRECISION`, nullable (NULL только в первых `warmup`-свечах окна).

| Группа | Колонки | Описание |
|--------|---------|----------|
| Returns | `return_1`, `return_6`, `return_24` | Процентное изменение цены за 1/6/24 свечи |
| Log returns | `log_return_1`, `log_return_6`, `log_return_24` | Логарифмический return |
| Rolling price stats | `price_roll6_mean/std/min/max` | Скользящие статистики за окно 6 свечей |
| Rolling price stats | `price_roll24_mean/std/min/max` | Скользящие статистики за окно 24 свечи |
| Price position | `price_to_roll6_mean`, `price_to_roll24_mean` | Цена / скользящее среднее |
| Realised volatility | `price_vol_6`, `price_vol_24` | Std(close) / Mean(close) за окно |
| OI momentum | `oi_roll6_mean`, `oi_roll24_mean` | Скользящее среднее открытого интереса |
| OI impulse | `oi_return_1` | Изменение OI за 1 свечу |
| RSI lag | `rsi_lag_1`, `rsi_lag_2` | RSI со сдвигом 1 и 2 свечи |
| Time features | `hour_sin`, `hour_cos`, `dow_sin`, `dow_cos` | Циклические признаки часа и дня недели |
| ATR | `atr_6`, `atr_24` | Average True Range за 6/24 свечи |
| Candle shape | `candle_body`, `upper_wick`, `lower_wick` | Форма тела и теней свечи |
| Volume rolling | `volume_roll6_mean`, `volume_roll24_mean` | Скользящее среднее объёма |
| Volume relative | `volume_to_roll6_mean`, `volume_to_roll24_mean` | Объём / скользящее среднее объёма |
| Volume momentum | `volume_return_1` | Изменение объёма за 1 свечу |
| RSI slope | `rsi_slope` | Изменение RSI за 1 свечу (`rsi − LAG(rsi, 1)`) |

---

## Аудит API топ-5 криптобирж

### Рейтинг по объёму деривативов (2025–2026)

| # | Биржа | Тип | Позиция на рынке |
|---|-------|-----|-----------------|
| 1 | **Binance** | CEX, spot + futures | Крупнейшая биржа, ~$50–70B суточный объём фьючерсов |
| 2 | **OKX** | CEX, spot + futures | Вторая по деривативам, сильная в Азии, ~$15–25B |
| 3 | **Bybit** | CEX, spot + futures | **Текущий источник данных**, ~$10–15B, сильная в деривативах |
| 4 | **Coinbase** | CEX, преимущественно spot | Крупнейшая в США, ~$2–5B (ограниченные деривативы) |
| 5 | **Kraken** | CEX, spot + ограниченные futures | Европейский лидер, ~$1–2B |

### Сравнительная таблица: доступность данных через REST API

| Данные | Bybit v5 | Binance Futures | OKX v5 | Coinbase Adv. | Kraken |
|--------|----------|-----------------|--------|---------------|--------|
| **OHLCV kline** | ✅ `/market/kline` | ✅ `/fapi/v1/klines` | ✅ `/market/candles` | ✅ (300 свечей/запрос) | ✅ (720 последних только) |
| **Open** | ✅ item[1] | ✅ | ✅ | ✅ | ✅ |
| **High** | ✅ item[2] | ✅ | ✅ | ✅ | ✅ |
| **Low** | ✅ item[3] | ✅ | ✅ | ✅ | ✅ |
| **Close** | ✅ item[4] | ✅ | ✅ | ✅ | ✅ |
| **Volume (base)** | ✅ item[5] | ✅ | ✅ | ✅ | ✅ |
| **Turnover (quote vol)** | ✅ item[6] | ✅ quoteVolume | ✅ volCcyQuote | ❌ | ❌ |
| **Число сделок на свечу** | ❌ | ✅ numTrades | ❌ | ❌ | ✅ count |
| **Taker buy volume** | ❌ REST | ✅ takerBuyBaseVolume | ✅ `/taker-volume` | ❌ | ❌ |
| **Funding Rate (история)** | ✅ `/market/funding/history` | ✅ `/fapi/v1/fundingRate` | ✅ `/funding-rate-history` | ❌ нет перпов | ❌ ограничено |
| **Open Interest (история)** | ✅ `/market/open-interest` | ✅ `/data/openInterestHist` | ✅ | ❌ | ❌ |
| **Long/Short Ratio** | ✅ без лимита истории | ⚠️ только 30 дней | ✅ | ❌ | ❌ |
| **Taker Buy/Sell Ratio** | ❌ | ✅ 30 дней | ✅ | ❌ | ❌ |
| **Implied Volatility** | ✅ (только опционы) | ✅ (только опционы) | ✅ (только опционы) | ❌ | ❌ |
| **Index Price kline** | ✅ `/market/index-price-kline` | ✅ | ✅ | ❌ | ❌ |
| **Mark Price kline** | ✅ `/market/mark-price-kline` | ✅ | ✅ | ❌ | ❌ |
| **Глубина истории** | ∞ (с запуска пары) | ∞ | ∞ | Ограничена | 720 свечей MAX |
| **Rate limit** | 120 запросов/с | 2400/мин | 20/2с | 10/с | 15/с |

### Что мы сейчас собираем от Bybit и что пропускаем

| Данные | Статус | Эндпоинт | Примечание |
|--------|--------|----------|------------|
| `index_price` (close) | ✅ Собирается | `/v5/market/kline` item[4] | Торговая цена закрытия |
| `open`, `high`, `low` | ✅ Собирается | `/v5/market/kline` item[1-3] | Полный OHLCV |
| `volume`, `turnover` | ✅ Собирается | `/v5/market/kline` item[5-6] | Объём торговли |
| `funding_rate` | ✅ Собирается | `/v5/market/funding/history` | Forward-fill на таймфрейм |
| `open_interest` | ✅ Собирается | `/v5/market/open-interest` | ✅ |
| `rsi` | ✅ Вычисляется | На стороне сервиса (Wilder RSI-14) | ✅ |
| Long/Short Ratio | ❌ Не собирается | `/v5/market/account-ratio` | История с 2020-07-20 |

### Feature-колонки которые можно вычислить при добавлении OHLCV

| Feature | Формула | Требует |
|---------|---------|---------|
| `atr_N` | `rolling_avg(high − low, N)` | high, low |
| `candle_body` | `|close − open| / close` | open |
| `upper_wick` | `(high − max(open, close)) / close` | open, high |
| `lower_wick` | `(min(open, close) − low) / close` | open, low |
| `volume_roll6/24_mean` | `rolling_avg(volume, N)` | volume |
| `volume_to_roll_mean` | `volume / volume_roll_mean` | volume |
| `volume_return_1` | `(vol_t − vol_{t-1}) / vol_{t-1}` | volume |
| `rsi_slope` | `rsi − LAG(rsi, 1)` | rsi (уже есть) |
| `funding_roll6/24_mean` | `rolling_avg(funding_rate, N)` | funding_rate (уже есть) |
| `vwap` | `cumsum(close × vol) / cumsum(vol)` | volume (rolling window) |

---

## Real-time данные (WebSocket only — ретроспективно недоступны)

Следующие данные **не имеют исторических REST API-эндпоинтов** и доступны только через WebSocket в реальном времени. Их **нельзя добавить ретроактивно** в датасет — только собирать начиная с момента подписки.

> 🚧 **Реализация не запланирована на текущем этапе.** Раздел — для будущего планирования.

| Данные | Bybit WS топик | Описание | Ценность для ML |
|--------|---------------|----------|-----------------|
| **Стакан заявок L2** | `orderbook.{depth}.{symbol}` | N уровней bid/ask с объёмами. Snapshot при подключении + инкрементальные delta | Order flow imbalance, bid-ask spread, ликвидность |
| **Лента сделок (tape)** | `publicTrade.{symbol}` | Каждая сделка: цена, объём, сторона (buy/sell), timestamp. Нет агрегации | CVD (Cumulative Volume Delta), delta volume, absorption |
| **Ликвидации** | `allLiquidation.{symbol}` | Принудительные ликвидации: цена, сторона, размер. Важный маркер каскадных движений | Детектор стресс-событий, liquidity grab |
| **CVD (Cumulative Volume Delta)** | Вычисляется из publicTrade WS | Накопленная разница: объём buy-taker − объём sell-taker | Сильный order flow сигнал, предиктор направления |
| **Bid/Ask спред real-time** | Orderbook WS | `ask_price − bid_price` в реальном времени | Индикатор ликвидности и режима рынка |
| **Price impact** | Вычисляется из Orderbook WS | Оценка slippage при сделке заданного объёма | Риск-метрика, ликвидность в стакане |
| **Tick-level order flow imbalance** | publicTrade WS + Orderbook WS | Дисбаланс объёмов на тик-уровне | Краткосрочный (< 1 мин) предиктор движения цены |

### Почему эти данные важны

- **Стакан (L2)** — позволяет строить `bid_ask_spread`, `orderbook_imbalance`, `depth_ratio`. Критично для liquidity zone и fake breakout детекции (из roadmap Tier 2/3).
- **Лента сделок** — единственный способ получить настоящий CVD. Все rolling volume метрики в текущем датасете (`volume_roll_mean` и т.д.) агрегированные и не отражают направленность потока.
- **Ликвидации** — прямой сигнал liquidity grab / cascade. REST API Bybit отдаёт только последние ~200 ликвидаций без глубокой истории.

---

## Roadmap

- ✅ **Step 1:** FastAPI skeleton + health/ready.
- ✅ **Step 1.5:** Kafka integration, `cmd.data.health` handler.
- ✅ **Step 2:** Dataset commands over Kafka; large payloads via MinIO claim-check; ingest pipeline; compute_features.
- ⏳ **Step 3:** Переход с `index-price-kline` на `kline` — добавить `open`, `high`, `low`, `volume`, `turnover` в сырую схему.
- ⏳ **Step 4:** Добавить Long/Short Ratio из `/v5/market/account-ratio`.
- ⏳ **Step 5:** WebSocket collector — real-time tape + orderbook для CVD и L2-признаков.
