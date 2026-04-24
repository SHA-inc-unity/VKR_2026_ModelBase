# ModelLine — Структура репозитория

Полное описание всех микросервисов, файлов, модулей и классов.

> **Детальная структура каждого сервиса** — в его собственном `STRUCTURE.md`.

---

## Архитектура платформы

```
                        ┌──────────────────────────────────────┐
                        │         microservice_infra           │
                        │  Redpanda (Kafka) :9092              │
                        │  MinIO (S3)       :9000              │
                        │  Nginx (proxy)    :80                │
                        │  modelline_net (docker bridge)       │
                        └──────────────┬───────────────────────┘
                                       │ Kafka IPC only
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
   ┌──────────▼──────────┐  ┌──────────▼──────────┐  ┌─────────▼─────────┐
   │ microservice_data   │  │ microservice_analitic│  │ microservice_admin│
   │ C#/.NET 8           │  │ Python 3.12          │  │ Next.js 14        │
   │ PostgreSQL :5433    │  │ FastAPI :8000        │  │ React/TS :3000    │
   │ Kafka consumer      │  │                      │  │ Admin UI :8501    │
   └─────────────────────┘  └──────────────────────┘  └───────────────────┘

   microservice_account (.NET 8, :5010)  ─── REST (JWT auth)
   microservice_gateway (.NET 8, :5020)  ─── Mobile BFF (HTTP routing)

   Nginx (в infra, :80): sha-trade.tech/admin → admin:3000
```

**Правило:** межсервисная коммуникация внутри ML-платформы — только Kafka. HTTP между сервисами запрещён. `account` и `gateway` — отдельный независимый стек (REST + JWT).

---

## Корень репозитория

| Файл / Папка | Описание |
|---|---|
| `README.md` | Главный README — быстрый старт, команды, описание сервисов |
| `STRUCTURE.md` | Этот файл — карта репозитория и архитектура |
| `.gitignore` | Git-правила: Python, .NET, Docker, IDE, OS, ML-артефакты |
| `microservicestarter/` | Единый менеджер запуска всех сервисов |
| `microservice_infra/` | Shared инфраструктура: Redpanda + MinIO |
| `microservice_analitic/` | ML-сервис: обучение, прогнозы (Python) |
| `microservice_data/` | Data-сервис: PostgreSQL + Kafka (C#/.NET 8) |
| `microservice_admin/` | Admin UI: Next.js + Kafka (TypeScript) |
| `microservice_account/` | Auth-сервис: JWT, аккаунты (C#/.NET 8) |
| `microservice_gateway/` | Mobile BFF Gateway (C#/.NET 8) |
| `shared/` | Общий Python-пакет `modelline_shared` (Kafka client, schemas) |

---

---

## microservicestarter/

Централизованные скрипты управления всеми микросервисами. Реестр сервисов — `services.conf`.

| Файл | Описание |
|---|---|
| `services.conf` | Реестр сервисов: `имя  относительный_путь` (по одному на строку) |
| `start.ps1` / `start.sh` | Запуск сервисов. При первом запуске создаёт `.env` и запрашивает пароль PostgreSQL. Собирает base-образ если нужен. После сборки удаляет dangling-образы Docker. |
| `stop.ps1` / `stop.sh` | Остановка контейнеров. Режимы: `stop` (default), `clean` (удалить volumes), `prune` (удалить образы). |
| `restart.ps1` / `restart.sh` | `git pull` + пересборка + перезапуск. Режимы: `core`, `full`, `deps`, `api`. После сборки удаляет dangling-образы. |
| `update.ps1` / `update.sh` | Только `git pull` без рестарта контейнеров. |
| `status.ps1` / `status.sh` | Показывает `docker compose ps` для каждого сервиса. |

### Режимы запуска / перезапуска

| Режим | Описание |
|---|---|
| `core` | Основной стек (по умолчанию) |
| `full` | Core + планировщик (scheduler profile) |
| `scheduler` | Только контейнер scheduler |
| `build` | Пересборка без кеша + запуск |
| `logs` | Live-логи (только `start`) |
| `api` | Пересобрать и перезапустить только api-контейнер (только `restart`) |
| `deps` | Пересобрать base-образ + зависимые сервисы (только `restart`) |
| `clean` | Остановить + удалить volumes — **СБРОС БД** (только `stop`) |
| `prune` | Остановить + удалить образы сервиса (только `stop`) |

---

## microservice_analitic/

**Стек:** Python 3.12, FastAPI, CatBoost, PostgreSQL, Redis (опционально). UI: microservice_admin (Next.js `:8501`)  
**Порты:** API `8000`  
**Docker:** multi-stage (`Dockerfile.base` → `Dockerfile.api`)

### Корень сервиса

| Файл | Описание |
|---|---|
| `docker-compose.yml` | Определяет сервисы `base` (profile `build-base`), `api`, `scheduler` (profile `scheduler`), `postgres`, `redis` (profile `with-redis`) |
| `Dockerfile.base` | Базовый образ Python с зависимостями (requirements.txt) |
| `Dockerfile.api` | FastAPI-сервер; FROM base |
| `.env.example` | Шаблон конфига: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `API_HOST/PORT`, `SCHEDULER_*` |
| `requirements.txt` | Python-зависимости |
| `README.md` | Документация сервиса |
| `scripts/build_dataset.py` | Скрипт CLI для единоразовой загрузки датасета в PostgreSQL |
| `scripts/train_catboost.py` | Скрипт CLI для обучения модели CatBoost вне Docker |

### backend/api/

| Файл | Ключевые объекты | Описание |
|---|---|---|
| `app.py` | `app` (FastAPI) | Создание FastAPI-приложения, подключение роутеров, CORS, lifespan |
| `run.py` | — | Точка входа uvicorn (`uvicorn backend.api.app:app`) |
| `schemas.py` | `TrainRequest`, `PredictRequest`, `DatasetStatusResponse`, `ModelInfoResponse`, … | Pydantic-схемы для запросов и ответов API |

### backend/dataset/

| Файл | Ключевые объекты | Описание |
|---|---|---|
| `api.py` | `DatasetApi` | HTTP-клиент к Bybit API для загрузки исторических свечей |
| `constants.py` | `TIMEFRAMES`, `DEFAULT_SYMBOL`, … | Константы: допустимые таймфреймы, символы, лимиты |
| `core.py` | `DatasetCore` | Загрузка, валидация и сохранение датасета в PostgreSQL |
| `database.py` | `Database` | Обёртка над `asyncpg` — пул соединений, выполнение запросов |
| `features.py` | `FeatureEngineer` | Расчёт технических признаков: скользящие средние, ATR, объёмы |
| `pipeline.py` | `DatasetPipeline` | Оркестратор: загрузка → features → сохранение |

### backend/model/

| Файл | Ключевые объекты | Описание |
|---|---|---|
| `cache.py` | `ModelCache` | In-memory кеш обученных моделей и их метаданных |
| `config.py` | `ModelConfig`, `TrainConfig`, `GridSearchConfig` | Конфиги модели и гиперпараметров (Pydantic BaseSettings) |
| `loader.py` | `ModelLoader` | Загрузка / сохранение `.cbm`-файлов CatBoost с диска |
| `metrics.py` | `ModelMetrics`, `calc_metrics()` | Расчёт метрик: MAE, RMSE, sign-accuracy, baseline-comparison |
| `pdf_report.py` | `PdfReportGenerator` | Генерация PDF-отчёта с метриками и графиками (ReportLab) |
| `report.py` | `ReportBuilder` | Сборка JSON-отчёта, запись в `models/` |
| `train.py` | `ModelTrainer` | Обучение CatBoost: split, fit, grid search, сохранение сессии |

### backend/

| Файл | Ключевые объекты | Описание |
|---|---|---|
| `scheduler.py` | `Scheduler`, `setup_scheduler()` | APScheduler-задачи: автообновление датасета, переобучение |
| `utils.py` | `get_logger()`, `format_duration()`, … | Вспомогательные утилиты: логирование, форматирование |

### frontend/ (microservice_analitic)

> UI переехал в `microservice_admin` (Next.js). Папка `frontend/` устарела и не используется.

### tests/ (microservice_analitic)

| Файл | Описание |
|---|---|
| `conftest.py` | Фикстуры pytest: мок-конфиги, мок-БД |
| `test_cache.py` | Тесты `ModelCache` |
| `test_metrics.py` | Тесты расчёта метрик |
| `test_pdf_report.py` | Тесты генерации PDF-отчёта |
| `test_session_roundtrip.py` | Тест сохранения и загрузки сессии модели |
| `test_utils.py` | Тесты утилит |

---

## microservice_account/

**Стек:** C#, .NET 8, ASP.NET Core, PostgreSQL, Redis (опционально), JWT, BCrypt  
**Порт:** `5010` → внутри контейнера `5000`  
**Архитектура:** Clean Architecture (Domain → Application → Infrastructure → API)

### Корень сервиса

| Файл | Описание |
|---|---|
| `AccountService.sln` | Solution-файл .NET |
| `Dockerfile` | Одноэтапная сборка с `dotnet publish` |
| `docker-compose.yml` | Сервисы: `account-api`, `postgres`, `redis` (profile `with-redis`) |
| `.env.example` | `POSTGRES_*`, `DATABASE_URL`, `JWT_*`, `BCRYPT_WORK_FACTOR`, `REDIS_URL`, `INTERNAL_API_KEY` |
| `global.json` | Привязка SDK; `"rollForward": "latestMajor"` — поддерживает SDK 10 |
| `README.md` | Документация сервиса, эндпоинты, переменные окружения |

### src/AccountService.Domain/

Чистая доменная модель без зависимостей на фреймворки.

| Файл / Папка | Описание |
|---|---|
| `Entities/User.cs` | Сущность пользователя: `Id`, `Email`, `PasswordHash`, `IsActive`, навигационные свойства |
| `Entities/Role.cs` | Сущность роли: `Id`, `Name` |
| `Entities/UserRole.cs` | Связь M:M пользователей и ролей |
| `Entities/RefreshToken.cs` | Refresh-токен: `Token`, `ExpiresAt`, `UserId`, `IsRevoked` |
| `Entities/AuditLoginEvent.cs` | Лог входов: `UserId`, `IpAddress`, `UserAgent`, `CreatedAt` |
| `Entities/UserSettings.cs` | Настройки пользователя (JSON-blob) |
| `Enums/` | Перечисления доменной области |

### src/AccountService.Application/

Бизнес-логика, интерфейсы, DTO. Не зависит от Infrastructure.

| Файл / Папка | Описание |
|---|---|
| `Services/AccountAppService.cs` | Главный сервис: регистрация, вход, выход, смена пароля, refresh |
| `Services/PasswordService.cs` | BCrypt-хеширование и верификация паролей |
| `Services/TokenService.cs` | Генерация и валидация JWT access- и refresh-токенов |
| `Interfaces/Repositories/` | Интерфейсы репозиториев (`IUserRepository`, `IRoleRepository`, `IRefreshTokenRepository`) |
| `Interfaces/Services/` | Интерфейсы сервисов (`IAccountAppService`, `IPasswordService`, `ITokenService`) |
| `Interfaces/Cache/` | Интерфейс кеша токенов (`ITokenCacheService`) |
| `DTOs/Requests/` | Pydantic-аналоги: `RegisterRequest`, `LoginRequest`, `RefreshTokenRequest`, … |
| `DTOs/Responses/` | `AuthResponse`, `UserProfileResponse`, … |
| `Validators/` | FluentValidation-валидаторы для Request-объектов |
| `Common/` | Общие типы: `Result<T>`, `Error`, `PagedList<T>` |

### src/AccountService.Infrastructure/

Реализации интерфейсов: EF Core, Redis, PostgreSQL.

| Файл / Папка | Описание |
|---|---|
| `Data/AccountDbContext.cs` | EF Core DbContext: DbSet-ы, конфигурации |
| `Data/Configurations/` | `IEntityTypeConfiguration<T>` для каждой сущности (индексы, FK, ограничения) |
| `Repositories/UserRepository.cs` | EF Core реализация `IUserRepository` |
| `Repositories/RoleRepository.cs` | EF Core реализация `IRoleRepository` |
| `Repositories/RefreshTokenRepository.cs` | EF Core реализация `IRefreshTokenRepository` |
| `Cache/RedisTokenCacheService.cs` | Redis-реализация `ITokenCacheService` (blacklist, TTL) |
| `Cache/NullTokenCacheService.cs` | No-op реализация `ITokenCacheService` (когда Redis недоступен) |
| `Migrations/` | EF Core миграции |

### src/AccountService.API/

| Файл / Папка | Описание |
|---|---|
| `Program.cs` | Точка входа: регистрация DI, middleware pipeline, EF migrations |
| `Controllers/AccountController.cs` | Публичные эндпоинты: `POST /api/account/register`, `/login`, `/refresh`, `/logout`, `/profile` |
| `Controllers/InternalController.cs` | Внутренние эндпоинты (для gateway): `/api/internal/validate-token`, `/api/internal/user/{id}` |
| `Middleware/GlobalExceptionMiddleware.cs` | Перехват необработанных исключений → ProblemDetails |
| `Extensions/ServiceCollectionExtensions.cs` | Регистрация зависимостей (DI), конфигурация JWT, Redis, EF |
| `Extensions/MigrationExtensions.cs` | Автозапуск EF-миграций при старте |
| `appsettings.json` / `appsettings.Development.json` | Конфигурация приложения |

### tests/ (microservice_account)

| Папка | Описание |
|---|---|
| `AccountService.UnitTests/` | Юнит-тесты сервисов и валидаторов (xUnit, Moq) |
| `AccountService.IntegrationTests/` | Интеграционные тесты с реальной БД (Testcontainers) |
| `AccountService.ContractTests/` | Контрактные тесты API (PactNet) |

---

## microservice_gateway/

**Стек:** C#, .NET 8, ASP.NET Core  
**Порт:** `5020`  
**Роль:** Mobile BFF — маршрутизация запросов от мобильного клиента к downstream-сервисам. Агрегирует данные из нескольких источников в один ответ.

### Корень сервиса

| Файл | Описание |
|---|---|
| `GatewayService.sln` | Solution-файл .NET |
| `Dockerfile` | Multi-stage сборка: `build` → `publish` → `runtime` |
| `docker-compose.yml` | Один сервис `gateway-service`; upstream account — `http://host.docker.internal:5010` |
| `.env.example` | `JWT_SECRET_KEY`, `JWT_ISSUER`, `JWT_AUDIENCE`, `ACCOUNT_SERVICE_URL`, `INTERNAL_GATEWAY_KEY` |
| `global.json` | Привязка SDK; `"rollForward": "latestMajor"` |
| `README.md` | Документация сервиса, эндпоинты, routing map |

### src/GatewayService.API/

#### Controllers/

| Файл | Описание |
|---|---|
| `AccountController.cs` | Проксирование запросов аутентификации к `microservice_account` |
| `AppController.cs` | Общие эндпоинты приложения (health, version) |
| `DashboardController.cs` | Агрегированные данные для главного экрана мобильного приложения |
| `NewsController.cs` | Проксирование к новостному downstream |
| `NotificationsController.cs` | Проксирование к сервису уведомлений |

#### Clients/

| Папка | Описание |
|---|---|
| `Account/` | `AccountServiceClient` — HTTP-клиент к `microservice_account` (Refit или HttpClient) |
| `Market/` | `MarketServiceClient` — клиент к рыночным данным |
| `News/` | `NewsServiceClient` — клиент к новостному сервису |
| `Notifications/` | `NotificationsServiceClient` — клиент к сервису уведомлений |
| `Portfolio/` | `PortfolioServiceClient` — клиент к портфельному сервису |

#### Aggregators/

| Папка | Описание |
|---|---|
| `Dashboard/` | `DashboardAggregator` — параллельный вызов нескольких клиентов, сборка dashboard-ответа |
| `Bootstrap/` | `BootstrapAggregator` — агрегация данных первого запуска приложения |

#### Middleware/

| Файл | Описание |
|---|---|
| `CorrelationIdMiddleware.cs` | Добавляет / пробрасывает `X-Correlation-Id` заголовок для трейсинга |
| `GlobalExceptionMiddleware.cs` | Обрабатывает необработанные исключения → `ProblemDetails` |

#### Settings/

| Файл | Описание |
|---|---|
| `DownstreamServicesSettings.cs` | URL и флаг `Enabled` для каждого downstream-сервиса |
| `FeatureFlagsSettings.cs` | Runtime feature-флаги (включение/отключение функций без редеплоя) |
| `JwtSettings.cs` | `SecretKey`, `Issuer`, `Audience`, сроки действия токенов |
| `ResilienceSettings.cs` | Polly: таймауты, retry, circuit breaker — параметры по сервисам |

#### Остальное

| Файл / Папка | Описание |
|---|---|
| `Common/` | Общие типы и вспомогательные классы |
| `DTOs/` | DTO запросов и ответов gateway |
| `Extensions/` | `IServiceCollection` extension methods — регистрация DI, HTTP-клиентов, Polly |
| `Program.cs` | Точка входа: конфигурация middleware pipeline, Swagger |
| `appsettings.json` / `appsettings.Development.json` | Конфигурация приложения |

### tests/ (microservice_gateway)

| Папка | Описание |
|---|---|
| `GatewayService.UnitTests/` | Юнит-тесты агрегаторов и клиентов |
| `GatewayService.IntegrationTests/` | Интеграционные тесты с мок-серверами (WireMock) |
| `GatewayService.ContractTests/` | Контрактные тесты API (PactNet) |
| `GatewayService.SmokeTests/` | Smoke-тесты: проверка доступности всех эндпоинтов |

---

## microservice_infra/

**Роль:** Shared инфраструктура платформы. Поднимается первым, создаёт `modelline_net`.  
**Детали:** [microservice_infra/STRUCTURE.md](microservice_infra/STRUCTURE.md)

| Компонент | Порт (host) | Назначение |
|-----------|------------|------------|
| Redpanda | `9092` | Kafka-API broker. Внутри сети: `redpanda:29092` |
| Redpanda Console | `8080` | Web UI топиков |
| MinIO | `9000` | S3 claim-check хранилище |
| MinIO Console | `9001` | Web UI MinIO |

---

## microservice_data/

**Стек:** C#, .NET 8, ASP.NET Core, PostgreSQL 16, Kafka (Redpanda), MinIO  
**Порт:** `8100`  
**Детали:** [microservice_data/STRUCTURE.md](microservice_data/STRUCTURE.md)

Единственный владелец рыночных данных. Все команды — через Kafka (`cmd.data.*`).

---

## microservice_admin/

**Стек:** Next.js 14, React 18, TypeScript, Tailwind, kafkajs  
**Порт:** `3000`  
**Детали:** [microservice_admin/STRUCTURE.md](microservice_admin/STRUCTURE.md)

Admin UI. Общается с `data` и `analytics` через Kafka (via server-side Route Handler proxy).

---

## shared/

**Роль:** Общий Python-пакет `modelline_shared` для ML-сервисов.  
**Детали:** [shared/STRUCTURE.md](shared/STRUCTURE.md)

| Модуль | Описание |
|--------|----------|
| `modelline_shared.schemas` | Shared Pydantic схемы (`HealthResponse`) |
| `modelline_shared.messaging.schemas` | `Envelope`, `HealthReply` |
| `modelline_shared.messaging.topics` | Все Kafka-топики как константы |
| `modelline_shared.messaging.client` | `KafkaClient` (aiokafka, request/reply) |

---

## Схема взаимодействия сервисов

```
Мобильный клиент
      │
      ▼ :5020
microservice_gateway  ──── JWT validation (local)
      │
      └──► http://host.docker.internal:5010  (microservice_account)
                └── account-api (ASP.NET Core) → PostgreSQL

Admin UI (Next.js :3000)
      │ POST /api/kafka (server-side proxy)
      ▼
   Redpanda (Kafka) ─────────────────────────────┐
      │                                           │
      ▼                                           ▼
microservice_data (.NET :8100)      microservice_analitic (Python :8000/:8501)
      └── PostgreSQL (market data)       └── CatBoost models
              │                                   │
              └─── MinIO (claim-check blobs) ─────┘
```

