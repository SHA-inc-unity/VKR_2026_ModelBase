# microservice_notification — Структура

> **Read before / update after.** Обновляй этот файл при изменении модулей,
> endpoints, notification kinds, Kafka-контрактов, схемы БД или delivery-модели.
> Полный docs-first маршрут — в [README.md](README.md) и
> [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md).

---

## Связанная документация

- [README.md](README.md) — runbook, delivery-модель, endpoints, kinds, config
- [../docs/agents/services/microservice_notification.md](../docs/agents/services/microservice_notification.md) — агентный профиль сервиса
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first workflow

---

## Что это

.NET 8 Clean Architecture сервис: per-user notification inbox + real-time SSE +
price-drift watcher. Своя PostgreSQL. Контейнер `notification_service_api`
(`7550 → 5000`), Postgres `notification_postgres`. **Consume-only** по Kafka
(`events.social.v1` / `events.news.v1`). Клиент ходит сюда только через gateway.

---

## Корень сервиса

| Файл | Описание |
| ---- | -------- |
| `NotificationService.sln` | Solution-файл .NET |
| `Dockerfile` | Multi-stage build → ASP.NET 8 runtime, non-root, `EXPOSE 5000` |
| `docker-compose.yml` | Локальный стек: `notification-api` (`7550→5000`) + `postgres` (bind mount `../.runtime-data/microservice_notification/postgres`); сети `notif_net` + external `modelline_net` |
| `global.json` | Привязка .NET SDK |
| `README.md` | Описание сервиса, delivery-модель, endpoints, config |
| `STRUCTURE.md` | Этот файл |

---

## src/

### `NotificationService.Domain/`

Чистая доменная модель без инфраструктурных зависимостей.

- `Entities/Notification.cs` — inbox-запись (`Id`, `UserId`, `Kind`, `Title`, `Body`, `Deeplink`, `PayloadJson`, `DedupKey`, `CreatedAt`, `ReadAt`); фабрика `Create(...)`, `MarkRead()`
- `Entities/NotificationSettings.cs` — per-user toggles (`EnableReply`/`EnableNews`/`EnablePrice` = `true`, `PriceThresholdPct` = `5`, clamp `0.0001..100`); `Default(userId)`, `Update(...)`

### `NotificationService.Application/`

Слой use-case, контрактов и DTO.

- `Services/NotificationsAppService.cs` — `INotificationsAppService`; **единственная точка fan-out** `PushAsync` (opt-out → dedup → persist → SSE), плюс list/unread-count/mark-read/mark-all + get/update settings
- `Interfaces/INotificationRepository.cs` — контракты репозиториев (`INotificationRepository`, `INotificationSettingsRepository`), `ISseDispatcher`, `ISocialDirectoryService`, `IMarketSnapshotService`, `NotificationListPage`
- `DTOs/NotificationDtos.cs` — list/unread/settings request-response модели
- `Common/Settings/NotificationServiceSettings.cs` — `JwtSettings`, `NotificationKafkaSettings`, `SocialServiceSettings`, `GatewaySettings`, `PriceWatcherSettings`

### `NotificationService.Infrastructure/`

Интеграция с PostgreSQL.

- `Data/NotificationDbContext.cs` — `DbSet<Notification>`, `DbSet<NotificationSettings>`
- `Data/Configurations/NotificationConfiguration.cs` — EF-маппинг таблиц `notifications` (индексы user+created, user+read, dedup; `payload_json` = jsonb) и `notification_settings` (PK = `user_id`)
- `Repositories/NotificationRepository.cs` — `NotificationRepository` (list/count/mark/dedup-exists) + `NotificationSettingsRepository` (`GetOrCreateAsync` с защитой от гонки insert, `SaveAsync`)
- `Migrations/` — EF Core миграции (`20260524000003_InitialCreate`) + snapshot

### `NotificationService.API/`

HTTP-, SSE-, Kafka- и background-входные точки.

- `Program.cs` — bootstrap, Serilog, middleware, `MapControllers` + `/health`, `await MigrateAndSeedAsync()`
- `Controllers/NotificationsController.cs` — `/api/notifications` (list, unread-count, `{id}/read`, read-all) + `GET stream` (SSE, `[AllowAnonymous]`, ручная валидация `access_token`)
- `Controllers/NotificationSettingsController.cs` — `/api/notification-settings` (GET/PUT)
- `Sse/SseDispatcher.cs` — **in-memory** singleton `ConcurrentDictionary<userId, clients>`; пишет `event: notification`; достаёт только подключённых клиентов
- `Kafka/KafkaConsumerService.cs` — `BackgroundService`, подписан на social+news topics; `comment.created` → `comment.reply`, `news.created` → `news.favorite`; парсит envelope `type`+`payload`, разрешает получателей через social `/internal/*`
- `BackgroundJobs/PriceDriftWatcherService.cs` — `BackgroundService`; polls favorited∪well-known symbols, сравнивает с последним in-memory снимком, пушит `price.favorite` за порогом (24 h dedup bucket)
- `Services/HttpSocialDirectoryService.cs` — social `/internal/comments/{id}/author`, `/internal/favorites/users-by-symbol/{sym}`, `/internal/favorites/symbols` (`X-Internal-Api-Key`)
- `Services/HttpMarketSnapshotService.cs` — gateway `GET /api/v1/market/snapshot?symbols=...`, soft-fail к пустой карте
- `Services/JwtTokenValidator.cs` — `IJwtTokenValidator.ResolveUserId(token)` для query-token пути SSE
- `Extensions/ServiceCollectionExtensions.cs` — DI: DbContext, репозитории, app-service, `SseDispatcher` singleton, HttpClients (social/gateway), оба hosted services, JWT bearer, Swagger, health checks
- `Extensions/MigrationExtensions.cs` — `MigrateAndSeedAsync`: `MigrateAsync` + `RequiredTables` whitelist (`notifications`, `notification_settings`); пустая схема → recreate from model, partial → hard-fail
- `Middleware/GlobalExceptionMiddleware.cs` — единый обработчик исключений
- `appsettings*.json` — конфигурация окружений

---

## tests/

Тестов **нет** — код является единственным источником истины.

---

## Что считать изменением структуры

- новые/изменённые HTTP endpoints или SSE-контракт
- новые notification kinds или изменение `PushAsync` fan-out (opt-out / dedup / persist / push)
- изменение consumed Kafka topics или формата envelope (`type`/`payload`)
- изменение схемы таблиц `notifications` / `notification_settings`, индексов, миграций
- изменение price-watcher логики, источников символов или порогов
- изменение config-секций, health-flow или зависимостей (social/gateway)
