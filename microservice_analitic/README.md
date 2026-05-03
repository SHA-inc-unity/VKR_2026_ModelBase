# microservice_analitic

**Роль:** ML-сервис платформы ModelLine. Обучение CatBoost-моделей прогнозирования доходности и REST API (FastAPI).

> **Архитектура:** Сервис работает только внутри контейнеров. Данные получает от `microservice_data` через Kafka (`cmd.data.dataset.*`). Команды управления обучением принимает через `cmd.analytics.*`. Прямого подключения к PostgreSQL нет — БД принадлежит `microservice_data`. UI: `microservice_admin` (Next.js).

## Документация для агентов

- [STRUCTURE.md](STRUCTURE.md) — карта backend-модулей, dataset/anomaly/model pipeline и API
- [../docs/agents/services/microservice_analitic.md](../docs/agents/services/microservice_analitic.md) — профиль сервиса для agent workflow
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first маршрут работы

### Kafka-обработчики (`cmd.analytics.*`)

`backend/data_client.py` поднимает `KafkaClient` сервиса `analitic` и подписывается на:

- `cmd.analytics.health` — liveness (обрабатывает `_handle_health`).
- `cmd.analytics.model.list` — список версий моделей. `_handle_model_list`
  вызывает `backend.model.report.load_registry(models_dir=MODELS_DIR, limit=1000)`
  и возвращает `{"models": [...]}` для `microservice_admin` (дашборд читает
  `response.models.length`).
- `cmd.analitic.dataset.load` — загрузка датасета в постоянную сессию
  (`backend/anomaly/session.py`). Один round-trip к DataService через
  `cmd.data.dataset.export_full` (`{symbol, timeframe, max_rows}`) — DataService
  сам резолвит таблицу, проверяет coverage, валидирует лимит строк и стримит CSV
  в MinIO. В ответе `{table_name, row_count, presigned_url}` (или прокинутая
  ошибка `table_not_found` / `empty_table` / `row_count_exceeds_limit`).
  Сервис стримит presigned URL по HTTP/2 (`httpx.AsyncClient(http2=True)`)
  во временный CSV (1 MB-чанками), магически детектит ZIP по сигнатуре
  `PK\x03\x04` и распаковывает, затем CSV → Parquet идёт через
  `pyarrow.csv.open_csv` (block_size 8 MiB) с per-RecordBatch downcast'ом
  `float64 → float32` на Arrow-уровне (без промежуточных pandas-фреймов) и
  `pyarrow.ParquetWriter(snappy)` append per batch на диск.
  Лимит: `MAX_SESSION_ROWS=5_000_000`. Хранилище: env `MODELLINE_SESSION_DIR`
  (default `/tmp/modelline_sessions`).
- `cmd.analitic.dataset.unload` — очистка сессии (`unlink` Parquet + `gc.collect`).
- `cmd.analitic.dataset.status` — состояние сессии (для admin badge).
- `cmd.analitic.anomaly.dbscan` — multivariate DBSCAN на загруженной сессии
  (`StandardScaler` + `sklearn.cluster.DBSCAN`). Систематический сэмпл до
  `max_sample_rows`, читает только нужные колонки через
  `pd.read_parquet(columns=…)`. Параметры: `eps=0.5`, `min_samples=5`,
  `max_sample_rows=50_000`, `columns=[close_price, volume, turnover, open_interest]`.
  Ответ: `{ summary: {…}, anomaly_timestamps_ms: [...] }`.
- `cmd.analitic.anomaly.isolation_forest` — Isolation Forest на загруженной
  сессии (`backend/anomaly/isolation_forest.py`). Tree-based detector
  (`sklearn.ensemble.IsolationForest`, `n_jobs=-1`, `random_state=42`).
  Параметры: `contamination=0.01` (`[1e-4, 0.5]`), `n_estimators=100` (`[20, 500]`),
  `max_sample_rows=50_000`, `columns=[close_price, volume, turnover, open_interest]`.
  Систематический сэмплинг для preserving temporal order (одинаковая стратегия
  что и в DBSCAN, чтобы UI мог сравнивать результаты двух методов). Ответ:
  `{ summary: { n_anomalies, contamination, n_estimators, … }, anomaly_timestamps_ms: [...] }`.
- `cmd.analitic.dataset.distribution` — диагностика распределения log-доходностей
  (`backend/anomaly/distribution.py`). Считает skewness, excess kurtosis (Fisher),
  Jarque-Bera statistic + p-value через `scipy.stats`. **Чтение данных
  чанк-выборкой нарушало бы математику log-returns** (на стыках выпадал бы
  diff между несоседними строками), поэтому `read_parquet_contiguous` тянет
  целые row-group'ы из хвоста файла — выдаёт ≥ `target_rows` строк, всегда
  смежных по `timestamp_utc`, без `shuffle`/`tail`/`stride`. Возвращает гистограмму
  log-returns ±5σ + точки нормальной кривой N(μ,σ), отскейленные под expected
  counts, а также `verdict` — текстовый вывод ("Heavy tails detected" /
  "Distribution appears compatible with normal" / `Sample too small`). Используется
  Anomaly → Distribution tab. JB надёжен при n ≥ 2000, иначе verdict
  предупреждает о ненадёжности.
- `cmd.analitic.dataset.quality_check` — аудит заполненности колонок
  (`backend/dataset/quality.py`). Запрашивает `cmd.data.dataset.column_stats`
  с `columns=[16 колонок QUALITY_GROUPS]` и `count_only=True` (только COUNT —
  без MIN/MAX/AVG/STDDEV, многократно быстрее на больших таблицах),
  агрегирует по трём группам:
  **OHLCV-сырые** (`open_price`, `high_price`, `low_price`, `volume`, `turnover`),
  **Производные от OHLCV** (`atr_6`, `atr_24`, `candle_body`, `upper_wick`,
  `lower_wick`, `volume_roll6_mean`, `volume_roll24_mean`, `volume_to_roll6_mean`,
  `volume_to_roll24_mean`, `volume_return_1`),
  **Производные от RSI** (`rsi_slope`).
  Для каждой группы возвращает `fill_pct = sum(non_null) * 100 / (total_rows * n_cols)`
  и статус: `≥99 → full`, `≥1 → partial`, `<1 → missing`. Каждая группа знает свой
  `repair_action` (`load_ohlcv` или `recompute_features`), который admin использует
  для соответствующей кнопки. Ответ:
  `{ table, total_rows, groups: [{ id, label, columns, fill_pct, status, repair_action }] }`.
- `cmd.analitic.dataset.load_ohlcv` — догрузка OHLCV-свечей через Bybit
  `/v5/market/kline` (`backend/dataset/repair.py`) **без перезаписи** остальных
  колонок таблицы. Pipeline: `prepare` (вызов `cmd.data.dataset.make_table`) →
  `fetch` (параллельный fan-out по `MAX_PARALLEL_API_WORKERS` окнам
  `PAGE_LIMIT_KLINE=1000`) → `upsert` (`cmd.data.dataset.upsert_ohlcv`,
  **батчами по `_UPSERT_BATCH_SIZE=4 500` строк** — каждое Kafka-сообщение
  ≈ 675 КБ × 150 б/строка < 1 МБ aiokafka-лимита).
  Таймаут на батч: `max(300, batch_size / 5000)` с (минимум **5 мин**,
  +1 с на каждые 5 000 строк батча). При ошибке любого батча операция
  немедленно прерывается. Прогресс стадии upsert отражает накопленный
  процент по всем батчам.
  Прогресс по стадиям публикуется в `events.analitic.dataset.repair.progress`.
  Payload: `{ symbol, timeframe, start_ms, end_ms, exchange="bybit"? }`.
  Ответ: `{ table, rows_affected, elapsed_sec }` или `{ error }`.
- `cmd.analitic.dataset.recompute_features` — пересчёт OHLCV-производных
  (`backend/dataset/repair.py`). Pipeline: `prepare` → `recompute`
  (`cmd.data.dataset.compute_features`). Таймаут recompute-запроса
  **адаптивный по таймфрейму**: `1m → 3600 с` (1 ч), `3m/5m → 1800 с` (30 мин),
  остальные → `600 с` (10 мин). Вынесено в константы `_RECOMPUTE_TIMEOUT` и
  `_RECOMPUTE_TIMEOUT_DEFAULT`.
  Прогресс в `events.analitic.dataset.repair.progress`.
  Payload: `{ symbol, timeframe }`. Ответ: `{ table, rows_updated, elapsed_sec }`.

### Kafka-события (`events.analitic.*`)

- `events.analitic.dataset.repair.progress` — стадии repair pipeline
  (`load_ohlcv` или `recompute_features`). Payload:
  `{ correlation_id, stage: 'prepare'|'fetch'|'upsert'|'recompute',
     label, status: 'running'|'done'|'error', progress: 0..100, detail? }`.

`data_client.get_coverage(table)` возвращает `{rows, min_ts_ms, max_ts_ms}` либо
`None` — парсит ответ `cmd.data.dataset.coverage` на верхнем уровне
(`exists`/`rows`/`min_ts_ms`/`max_ts_ms`) без вложенного ключа `coverage`.

**Активный стек (Docker):** FastAPI `:8000`, Redis (опционально `:6379`). Сеть: `modelline_net` (внешняя, создаётся `microservice_infra`).

---

## Архитектура

```text
┌──────────────────────────────────────────────────────────────┐
│                     Docker Compose                            │
│                                                              │
│  redis:6379  ←──┐                                            │
│                 ├──  api:8000      (FastAPI REST)             │
│                 └──  scheduler     (переобучение по cron)     │
│                                                              │
│  microservice_data ──Kafka──▶ data_client (внутри api/sched) │
└──────────────────────────────────────────────────────────────┘
```

| Сервис             | Адрес            | Описание                            |
|--------------------|------------------|-------------------------------------|
| REST API           | `localhost:8000` | FastAPI + Swagger `/docs`           |
| Redis              | `localhost:6379` | KV-store настроек (fallback SQLite) |
| microservice_data  | Kafka (внутри)   | Источник данных (PostgreSQL, Bybit) |

---

## Быстрый старт — Docker (рекомендуется)

### Требования

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / macOS / Linux)
- Docker Engine запущен

### 1. Настройка окружения

Скопируйте `.env.example` в `.env` и при необходимости задайте `KAFKA_BOOTSTRAP_SERVERS`:

```bash
cp .env.example .env
```

Скрипты запуска создадут `.env` автоматически при первом старте, если файла нет.

### 2. Запуск

**Linux / macOS:**

```bash
chmod +x microservicestarter/start.sh microservicestarter/stop.sh microservicestarter/restart.sh
./microservicestarter/start.sh
```

**Windows (PowerShell):**

```powershell
.\microservicestarter\start.ps1
```

После запуска откройте в браузере: **`http://localhost:8501`** (microservice_admin)

### 3. Остановка

**Linux / macOS:**

```bash
./microservicestarter/stop.sh
```

**Windows (PowerShell):**

```powershell
.\microservicestarter\stop.ps1
```

### 4. Перезапуск после изменения кода

Если вы изменили код и хотите подтянуть изменения в Docker:

**Linux / macOS:**

```bash
./microservicestarter/restart.sh          # пересобрать и перезапустить core
./microservicestarter/restart.sh api      # только API
```

**Windows (PowerShell):**

```powershell
.\microservicestarter\restart.ps1           # пересобрать и перезапустить core
.\microservicestarter\restart.ps1 api       # только API
```

---

## Режимы запуска

Скрипты принимают необязательный аргумент режима.

**start.sh / start.ps1:**

| Аргумент    | Что запускает                                   |
|-------------|-------------------------------------------------|
| *(нет)*     | Core: postgres + redis + api                    |
| `full`      | Core + scheduler (переобучение по расписанию)   |
| `scheduler` | Только scheduler (core уже должен быть запущен) |
| `build`     | Пересборка образов + запуск core                |
| `logs`      | Live-логи всех сервисов                         |

**restart.sh / restart.ps1:**

| Аргумент    | Что делает                                                        | Скорость |
|-------------|-------------------------------------------------------------------|----------|
| *(нет)*     | Пересобрать код и перезапустить core                              | ~5 с     |
| `full`      | Пересобрать код и перезапустить core + scheduler                  | ~5 с     |
| `api`       | Пересобрать и перезапустить только API                            | ~3 с     |

| `deps`      | Пересобрать базовый образ (при изменении `requirements.txt`)      | ~2 мин   |

> **Как работает кеш:** зависимости Python хранятся в базовом образе `modelline-base`.
> При изменении только кода пересобирается лишь последний слой `COPY . .` — это занимает секунды.
> Запускайте `restart deps` только когда меняете `requirements.txt`.

**stop.sh / stop.ps1:**

| Аргумент | Что делает                                              |
|----------|---------------------------------------------------------|
| *(нет)*  | Остановить сервисы (данные и модели сохраняются)        |
| `clean`  | Остановить + **удалить volumes** (БД и модели!)         |
| `prune`  | Остановить + удалить образы (освобождает место на диске)|

---

## Scheduler — автоматическое переобучение

Чтобы модели переобучались по расписанию, задайте задания в `.env`:

```env
SCHEDULER_JOBS=[{"symbol":"BTCUSDT","timeframe":"60m","cron":"0 3 * * *","use_gpu":false,"target_col":"target_return_1"},{"symbol":"ETHUSDT","timeframe":"60m","cron":"30 3 * * *","use_gpu":false}]
```

Затем запустите с профилем:

```bash
# Linux / macOS
./microservicestarter/start.sh full

# Только scheduler поверх уже запущенного core:
./microservicestarter/start.sh scheduler
```

```powershell
# Windows
.\microservicestarter\start.ps1 full
```

---

## REST API

Swagger UI: **`http://localhost:8000/docs`**

| Метод  | Путь                     | Описание                          |
|--------|--------------------------|-----------------------------------|
| GET    | `/health`                | Проверка доступности              |
| GET    | `/registry`              | Список версий обученных моделей   |
| DELETE | `/registry/{version_id}` | Удалить запись из реестра         |
| GET    | `/predictions/{prefix}`  | Предсказания последней сессии     |
| GET    | `/metrics/{prefix}`      | Метрики последней версии модели   |
| POST   | `/retrain`               | Запустить переобучение через REST |
| GET    | `/scheduler/status`      | Статус планировщика               |

Пример запроса переобучения:

```bash
curl -X POST http://localhost:8000/retrain \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","timeframe":"60m","use_gpu":false}'
```

---

## Переменные окружения

Все параметры задаются в `.env` (скопируйте из `.env.example`):

| Переменная                | По умолчанию               | Описание                                        |
|---------------------------|----------------------------|-------------------------------------------------|
| `KAFKA_BOOTSTRAP_SERVERS` | `redpanda:29092`           | Kafka (Redpanda) — источник данных              |
| `REDIS_URL`               | `redis://localhost:6379/0` | URL Redis                                       |
| `API_PORT`                | `8000`                     | Порт FastAPI                                    |
| `SCHEDULER_JOBS`          | `[]`                       | JSON-список заданий scheduler                   |
| `SCHEDULER_TIMEZONE`      | `UTC`                      | Временная зона для cron                         |

---

## Структура проекта

```text
ModelLine/
├── backend/
│   ├── api/            # FastAPI REST (app.py, schemas.py, run.py)
│   ├── dataset/        # Загрузка и обработка рыночных данных
│   ├── model/          # CatBoost: обучение, метрики, реестр, кеш
│   └── scheduler.py    # APScheduler — переобучение по расписанию
├── microservicestarter/
│   ├── start.sh        # Запуск (Linux/macOS)
│   ├── stop.sh         # Остановка (Linux/macOS)
│   ├── restart.sh      # Перезапуск после изменений кода (Linux/macOS)
│   ├── start.ps1       # Запуск (Windows PowerShell)
│   ├── stop.ps1        # Остановка (Windows PowerShell)
│   └── restart.ps1     # Перезапуск после изменений кода (Windows PowerShell)
├── tests/              # pytest — 234 теста
├── docker-compose.yml
├── Dockerfile.api
├── requirements.txt
└── .env.example
```

---

## Полезные команды Docker

```bash
# Статус сервисов
docker compose ps

# Логи конкретного сервиса
docker compose logs -f api

# Зайти в контейнер
docker compose exec api bash
```
