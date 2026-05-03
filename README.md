# ModelLine — Микросервисная архитектура

Монорепозиторий платформы ModelLine. В runtime-контур входят инфраструктурный слой, сервис данных, admin UI, ML-сервис аналитики, сервис аккаунтов и mobile gateway. Общий запуск, остановка и обновление сервисов выполняет **microservicestarter**.

---

## Runtime-сервисы

| Сервис | Технологии | Порты | Описание |
|--------|-----------|-------|----------|
| [microservice_infra](microservice_infra/README.md) | Docker Compose, Redpanda, MinIO, Nginx | `9092`, `8080`, `9000`, `9001`, `80` | Общая инфраструктура платформы: Kafka, S3 claim-check, reverse proxy |
| [microservice_data](microservice_data/README.md) | .NET 8, ASP.NET Core, PostgreSQL, Kafka, MinIO | `8100` | Владелец рыночных данных, датасета, export и фоновых jobs |
| [microservice_admin](microservice_admin/README.md) | Next.js 14, React 18, TypeScript, Kafka, Redis | `8501` внешне, `3000` внутри | Admin UI и операторская панель платформы; не исполняет jobs, а только управляет и наблюдает jobs других сервисов |
| [microservice_analitic](microservice_analitic/README.md) | Python 3.12, CatBoost, FastAPI, PostgreSQL, Redis | API: `8000` | ML-сервис: обучение, прогнозы, аналитика рынка |
| [microservice_account](microservice_account/README.md) | .NET 8, ASP.NET Core, PostgreSQL, Redis | `5010` | Сервис аутентификации и управления аккаунтами (Clean Architecture) |
| [microservice_gateway](microservice_gateway/README.md) | .NET 8, ASP.NET Core | `5020` | Mobile BFF Gateway — маршрутизация и агрегация запросов |

---

## Общие каталоги

| Каталог | Назначение |
|--------|------------|
| [microservicestarter](microservicestarter/README.md) | Единый launcher и операционные скрипты для всех сервисов |
| [shared](shared/README.md) | Общий Python-пакет с контрактами и messaging-утилитами |
| [docs/agents](docs/agents/README.md) | Docs-first структура и маршрут чтения для агентной разработки |

---

## Структура репозитория

```
/
├── AGENTS.md                    # Глобальные правила работы агентов
├── promt_agent.md               # Краткий рабочий дневник агента
├── docs/agents/                 # Markdown-опоры для агентной разработки
├── .github/instructions/        # Агентские file instructions
│
├── microservice_infra/          # Redpanda + MinIO + Nginx + shared network
├── microservice_data/           # Data service (.NET 8, PostgreSQL, Kafka jobs)
├── microservice_admin/          # Admin UI (Next.js, Kafka, Redis)
├── microservice_analitic/       # ML service (Python, FastAPI, training, anomaly)
├── microservice_account/        # Auth service (.NET 8, JWT, PostgreSQL)
├── microservice_gateway/        # Mobile BFF gateway (.NET 8)
├── microservicestarter/         # Общий менеджер для всех микросервисов
├── shared/                      # Общий Python-пакет `modelline_shared`
├── README.md                    # Этот файл
└── STRUCTURE.md                 # Корневая карта репозитория
```

---

## Документация для агентов

В репозитории включён docs-first workflow для агентной разработки.

- [AGENTS.md](AGENTS.md) — глобальное правило: читать Markdown до работы с кодом и обновлять Markdown после работы с кодом.
- [promt_agent.md](promt_agent.md) — краткий рабочий дневник агента; обязателен к чтению перед работой и к обновлению после работы.
- [docs/agents/README.md](docs/agents/README.md) — индекс агентной документации.
- [docs/agents/WORKFLOW.md](docs/agents/WORKFLOW.md) — обязательный маршрут работы агента.
- [docs/agents/DOCS_MAP.md](docs/agents/DOCS_MAP.md) — карта документов по репозиторию.
- [docs/agents/services/README.md](docs/agents/services/README.md) — сервисные профили и обязательные документы для чтения.

---

## Быстрый старт

### Требования

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (запущен)
- .NET SDK ≥ 8.0 (для сборки .NET-сервисов без Docker)
- Python 3.12+ (для локального запуска аналитики без Docker)

### Linux / macOS

```bash
cd microservicestarter/

# Запуск всех сервисов
./start.sh

# Запуск конкретного сервиса
./start.sh microservice_infra
./start.sh microservice_data
./start.sh microservice_admin
./start.sh microservice_analitic
./start.sh microservice_account
./start.sh microservice_gateway

# Остановка
./stop.sh                                # все сервисы
./stop.sh microservice_analitic clean    # остановить + удалить volumes (СБРОС БД!)

# Перезапуск с git pull
./restart.sh
./restart.sh microservice_analitic

# Статус контейнеров
./status.sh
./status.sh microservice_analitic
```

### Windows (PowerShell)

```powershell
cd microservicestarter\

# Запуск всех сервисов
.\start.ps1

# Запуск конкретного сервиса
.\start.ps1 -Service microservice_infra
.\start.ps1 -Service microservice_data
.\start.ps1 -Service microservice_admin
.\start.ps1 -Service microservice_analitic
.\start.ps1 -Service microservice_account
.\start.ps1 -Service microservice_gateway

# Остановка
.\stop.ps1
.\stop.ps1 -Service microservice_analitic -Mode clean  # остановить + удалить volumes (СБРОС БД!)

# Перезапуск с git pull
.\restart.ps1
.\restart.ps1 -Service microservice_analitic

# Статус
.\status.ps1
.\status.ps1 -Service microservice_analitic
```

---

## Режимы запуска и перезапуска

| Режим       | Описание                                                           |
|-------------|--------------------------------------------------------------------|
| `core`      | Запуск основного стека — **по умолчанию**                          |
| `full`      | Core + планировщик переобучения (scheduler)                        |
| `scheduler` | Только scheduler (требует запущенного core)                        |
| `build`     | Пересборка образов без кеша + запуск                               |
| `logs`      | Показать live-логи (только `start`)                                |
| `api`       | Пересобрать и перезапустить только api-контейнер (только `restart`)|
| `deps`      | Пересобрать base-образ + всё сверху (только `restart`)             |
| `clean`     | Остановить + удалить volumes (только `stop`) — **СБРОС БД!**       |
| `prune`     | Остановить + удалить Docker-образы сервиса (только `stop`)         |

---

## Переменные окружения

При **первом запуске** через `start.sh` / `start.ps1` launcher автоматически создаёт `.env` из `.env.example` в тех сервисах, где это поддерживается, и запрашивает обязательные секреты.

Если нужно подготовить окружение вручную, ориентируйся на `README.md` конкретного сервиса и его `.env.example`.

---

## Добавление нового микросервиса

1. Создайте папку сервиса в корне репозитория и сразу добавьте в неё как минимум `README.md`, `STRUCTURE.md`, `docker-compose.yml` и, при необходимости, `.env.example`.
2. Зарегистрируйте сервис в `microservicestarter/services.conf`:
   ```
   my_new_service  my_new_service
   ```
3. Обновите [STRUCTURE.md](STRUCTURE.md), [docs/agents/DOCS_MAP.md](docs/agents/DOCS_MAP.md) и профиль в `docs/agents/services/`, чтобы новый сервис вошёл в docs-first маршрут чтения.
4. Готово — все команды `start/stop/restart/status` автоматически увидят новый сервис.
