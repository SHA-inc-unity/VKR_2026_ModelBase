# microservice_analitic

**Роль:** ML-сервис платформы ModelLine. Обучение CatBoost-моделей прогнозирования доходности и REST API (FastAPI).

> **Архитектура:** Сервис работает только внутри контейнеров. Данные получает от `microservice_data` через Kafka (`cmd.data.dataset.*`). Команды управления обучением принимает через `cmd.analytics.*`. Прямого подключения к PostgreSQL нет — БД принадлежит `microservice_data`. UI: `microservice_admin` (Next.js).

### Kafka-обработчики (`cmd.analytics.*`)

`backend/data_client.py` поднимает `KafkaClient` сервиса `analitic` и подписывается на:

- `cmd.analytics.health` — liveness (обрабатывает `_handle_health`).
- `cmd.analytics.model.list` — список версий моделей. `_handle_model_list`
  вызывает `backend.model.report.load_registry(models_dir=MODELS_DIR, limit=1000)`
  и возвращает `{"models": [...]}` для `microservice_admin` (дашборд читает
  `response.models.length`).

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
