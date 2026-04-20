# ModelLine

CatBoost-модель для прогнозирования доходности криптовалютных инструментов.  
Данные хранятся в PostgreSQL, интерфейс — Streamlit, REST API — FastAPI.

---

## Архитектура

```text
┌─────────────────────────────────────────────────────────┐
│                     Docker Compose                       │
│                                                         │
│  postgres:5432  ←──┐                                    │
│  redis:6379     ←──┼──  api:8000   (FastAPI REST)       │
│                    ├──  streamlit:8501  (UI)             │
│                    └──  scheduler  (переобучение по cron)│
└─────────────────────────────────────────────────────────┘
```

| Сервис     | Адрес                  | Описание                            |
|------------|------------------------|-------------------------------------|
| Streamlit  | `localhost:8501`       | Web-UI: данные, обучение, сравнение |
| REST API   | `localhost:8000`       | FastAPI + Swagger `/docs`           |
| PostgreSQL | `localhost:5432`       | Хранение рыночных данных            |
| Redis      | `localhost:6379`       | KV-store настроек (fallback SQLite) |

---

## Быстрый старт — Docker (рекомендуется)

### Требования

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / macOS / Linux)
- Docker Engine запущен

### 1. Настройка окружения

Скопируйте `.env.example` в `.env` и укажите пароль PostgreSQL:

```bash
cp .env.example .env
# Откройте .env и заполните PGPASSWORD
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

После запуска откройте в браузере: **`http://localhost:8501`**

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
./microservicestarter/restart.sh streamlit  # только Streamlit
```

**Windows (PowerShell):**

```powershell
.\microservicestarter\restart.ps1           # пересобрать и перезапустить core
.\microservicestarter\restart.ps1 api       # только API
.\microservicestarter\restart.ps1 streamlit # только Streamlit
```

---

## Режимы запуска

Скрипты принимают необязательный аргумент режима.

**start.sh / start.ps1:**

| Аргумент    | Что запускает                                   |
|-------------|-------------------------------------------------|
| *(нет)*     | Core: postgres + redis + api + streamlit        |
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
| `streamlit` | Пересобрать и перезапустить только Streamlit                      | ~3 с     |
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

## Запуск без Docker (локальная разработка)

### Зависимости Python

- Python 3.11+
- PostgreSQL доступен

```bash
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### Запуск Streamlit UI

```bash
streamlit run frontend/app.py
```

### Запуск FastAPI

```bash
python -m backend.api.run
# или
uvicorn backend.api.app:app --host 0.0.0.0 --port 8000 --reload
```

### Запуск Scheduler

```bash
# Задайте SCHEDULER_JOBS в .env, затем:
python -m backend.scheduler
```

---

## Переменные окружения

Все параметры задаются в `.env` (скопируйте из `.env.example`):

| Переменная           | По умолчанию               | Описание                           |
|----------------------|----------------------------|------------------------------------|
| `PGHOST`             | `localhost`                | Хост PostgreSQL                    |
| `PGPORT`             | `5432`                     | Порт PostgreSQL                    |
| `PGDATABASE`         | `crypt_date`               | Имя базы данных                    |
| `PGUSER`             | `postgres`                 | Пользователь PostgreSQL            |
| `PGPASSWORD`         | —                          | Пароль PostgreSQL (**обязателен**) |
| `REDIS_URL`          | `redis://localhost:6379/0` | URL Redis                          |
| `API_PORT`           | `8000`                     | Порт FastAPI                       |
| `SCHEDULER_JOBS`     | `[]`                       | JSON-список заданий scheduler      |
| `SCHEDULER_TIMEZONE` | `UTC`                      | Временная зона для cron            |

---

## Структура проекта

```text
ModelLine/
├── backend/
│   ├── api/            # FastAPI REST (app.py, schemas.py, run.py)
│   ├── dataset/        # Загрузка и обработка рыночных данных
│   ├── model/          # CatBoost: обучение, метрики, реестр, кеш
│   └── scheduler.py    # APScheduler — переобучение по расписанию
├── frontend/
│   ├── app.py          # Главная страница Streamlit
│   ├── pages/          # download_page, model_page, compare_page
│   └── services/       # trainer, store, db_auth, colors, charts
├── microservicestarter/
│   ├── start.sh        # Запуск (Linux/macOS)
│   ├── stop.sh         # Остановка (Linux/macOS)
│   ├── restart.sh      # Перезапуск после изменений кода (Linux/macOS)
│   ├── start.ps1       # Запуск (Windows PowerShell)
│   ├── stop.ps1        # Остановка (Windows PowerShell)
│   └── restart.ps1     # Перезапуск после изменений кода (Windows PowerShell)
├── scripts/            # Утилиты: build_dataset.py, train_catboost.py
├── tests/              # pytest — 234 теста
├── docker-compose.yml
├── Dockerfile.api
├── Dockerfile.streamlit
├── requirements.txt
└── .env.example
```

---

## Полезные команды Docker

```bash
# Статус сервисов
docker compose ps

# Логи конкретного сервиса
docker compose logs -f streamlit
docker compose logs -f api

# Перезапустить один сервис
docker compose restart streamlit

# Зайти в контейнер
docker compose exec api bash
docker compose exec postgres psql -U postgres -d crypt_date
```
