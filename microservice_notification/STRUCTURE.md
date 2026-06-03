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
- `Entities/PushSubscription.cs` — browser Web Push (VAPID) subscription (`Id`, `UserId`, `Endpoint` unique, `P256dh`, `Auth`, `UserAgent?`, `CreatedAt`, `LastSeenAt`, `FailureCount`); `Create(...)`, `Refresh(...)`
- `Entities/PriceAlert.cs` — user-defined price alert (`Id`, `UserId`, `Symbol` upper, `Condition` `above`/`below`, `TargetPrice`, `IsEnabled`, `IsArmed` default `true`, `LastTriggeredAt?`, `LastObservedPrice?`, `CreatedAt`, `UpdatedAt`); `Create(...)` (валидирует condition, upper-symbol), `Update(...)` (re-arm при смене target/condition или re-enable), `MarkFired(price)` (disarm+stamp), `ReArm()`

### `NotificationService.Application/`

Слой use-case, контрактов и DTO.

- `Services/NotificationsAppService.cs` — `INotificationsAppService`; **единственная точка fan-out** `PushAsync` (opt-out → dedup → persist → SSE → **Web Push best-effort**), плюс list/unread-count/mark-read/mark-all + get/update settings. Opt-out switch: `price.alert` тоже мапится на `EnablePrice`
- `Services/PriceAlertsAppService.cs` — `IPriceAlertsAppService`; CRUD для `/api/alerts` (list/create/update/delete, ownership-scoped), маппинг в `AlertResponse` (`Id` = `Guid.ToString("N")`, `CreatedAt` = UTC `DateTimeOffset`); валидация condition делегируется домену (`ArgumentException` → 400)
- `Interfaces/INotificationRepository.cs` — контракты репозиториев (`INotificationRepository`, `INotificationSettingsRepository`, `IPushSubscriptionRepository`, `IPriceAlertRepository`), `ISseDispatcher`, `IWebPushSender`, `ISocialDirectoryService`, `IMarketSnapshotService`, `NotificationListPage`
- `DTOs/NotificationDtos.cs` — list/unread/settings request-response модели + push DTO (`PushPublicKeyResponse`, `PushSubscribeRequest`/`PushSubscriptionKeysDto`, `PushUnsubscribeRequest`)
- `DTOs/PriceAlertDtos.cs` — `AlertResponse` (wire-shape `{ id, symbol, condition, targetPrice, isEnabled, createdAt }`, совместим с gateway `PriceAlertDto`), `CreateAlertRequest`, `UpdateAlertRequest`
- `Common/Settings/NotificationServiceSettings.cs` — `JwtSettings`, `NotificationKafkaSettings`, `SocialServiceSettings`, `GatewaySettings`, `PriceWatcherSettings`, `AlertWatcherSettings` (poll/enabled), `PushSettings` (VAPID public/private/subject + computed `Enabled`)

### `NotificationService.Infrastructure/`

Интеграция с PostgreSQL.

- `Data/NotificationDbContext.cs` — `DbSet<Notification>`, `DbSet<NotificationSettings>`, `DbSet<PushSubscription>`, `DbSet<PriceAlert>`
- `Data/Configurations/NotificationConfiguration.cs` — EF-маппинг таблиц `notifications` (индексы user+created, user+read, dedup; `payload_json` = jsonb), `notification_settings` (PK = `user_id`), `push_subscriptions` (`PushSubscriptionConfiguration`: PK `id`, UNIQUE `endpoint`, индекс `user_id`) и `price_alerts` (`PriceAlertConfiguration`: PK `id`, `numeric` для `target_price`/`last_observed_price`, maxlen symbol(32)/condition(8), индексы `ix_price_alerts_user_id`, `ix_price_alerts_is_enabled`)
- `Repositories/NotificationRepository.cs` — `NotificationRepository` (list/count/mark/dedup-exists) + `NotificationSettingsRepository` (`GetOrCreateAsync` с защитой от гонки insert, `SaveAsync`) + `PushSubscriptionRepository` (`UpsertAsync` by endpoint, `DeleteByEndpointAsync`, `ListByUserAsync`, `DeleteAsync`, `IncrementFailureAsync`)
- `Repositories/PriceAlertRepository.cs` — `PriceAlertRepository` (`ListByUserAsync`, `GetAsync` ownership-scoped, `AddAsync`, `UpdateAsync`, `DeleteAsync` ownership-scoped, `ListEnabledAsync` — cross-user batch для evaluator)
- `PushNotifications/WebPushSender.cs` — `IWebPushSender` impl на `WebPush` NuGet (`WebPushClient`+`VapidDetails`); payload `{title,body,deeplink,kind,id}`; best-effort, never throws; dead-sub cleanup на `404`/`410`, `failure_count`++ на прочих ошибках; disabled (logged once) при пустом private key
- `Migrations/` — EF Core миграции (`20260524000003_InitialCreate`, `20260603000002_AddPushSubscriptions`, `20260603000003_AddPriceAlerts`) + snapshot

### `NotificationService.API/`

HTTP-, SSE-, Kafka- и background-входные точки.

- `Program.cs` — bootstrap, Serilog, middleware, `MapControllers` + `/health`, `await MigrateAndSeedAsync()`
- `Controllers/NotificationsController.cs` — `/api/notifications` (list, unread-count, `{id}/read`, read-all) + `GET stream` (SSE, `[AllowAnonymous]`, ручная валидация `access_token`) + Web Push (`GET push/public-key` `[AllowAnonymous]`, `POST push/subscribe`, `POST push/unsubscribe`)
- `Controllers/NotificationSettingsController.cs` — `/api/notification-settings` (GET/PUT)
- `Controllers/AlertsController.cs` — `/api/alerts` `[Authorize]` (GET list, POST create, PATCH `{id}` update→404 if not owned, DELETE `{id}`→204/404); userId = Guid из claims, всё scoped по userId; route-`{id}` парсится из `"N"`-form Guid
- `Sse/SseDispatcher.cs` — **in-memory** singleton `ConcurrentDictionary<userId, clients>`; пишет `event: notification`; достаёт только подключённых клиентов
- `Kafka/KafkaConsumerService.cs` — `BackgroundService`, подписан на social+news topics; `comment.created` → `comment.reply`, `news.created` → `news.favorite`; парсит envelope `type`+`payload`, разрешает получателей через social `/internal/*`
- `BackgroundJobs/PriceDriftWatcherService.cs` — `BackgroundService`; polls favorited∪well-known symbols, сравнивает с последним in-memory снимком, пушит `price.favorite` за порогом (24 h dedup bucket)
- `BackgroundJobs/PriceAlertEvaluatorService.cs` — `BackgroundService` (клон cadence/kill-switch `PriceDriftWatcherService`); `AlertWatcher:Enabled` (default true) + `PollIntervalSeconds` (default 60, floor 30), 30 s warm-up. Каждый tick: `ListEnabledAsync` → `IMarketSnapshotService.GetSnapshotAsync(distinct symbols)` → для алерта `met = above?p>=t:p<=t`; `met && IsArmed` → fire `price.alert` (`PushAsync`) + `MarkFired` (fire-once); `!met && !IsArmed` → `ReArm`. dedup `alert:{id}:{firedEpoch}`, tick soft-fail. Переиспользует существующий `IMarketSnapshotService`
- `Services/HttpSocialDirectoryService.cs` — social `/internal/comments/{id}/author`, `/internal/favorites/users-by-symbol/{sym}`, `/internal/favorites/symbols` (`X-Internal-Api-Key`)
- `Services/HttpMarketSnapshotService.cs` — gateway `GET /api/v1/market/snapshot?symbols=...`, soft-fail к пустой карте
- `Services/JwtTokenValidator.cs` — `IJwtTokenValidator.ResolveUserId(token)` для query-token пути SSE
- `Extensions/ServiceCollectionExtensions.cs` — DI: DbContext, репозитории (вкл. `IPushSubscriptionRepository`, `IPriceAlertRepository`), `IWebPushSender`, `PushSettings`, `AlertWatcherSettings`, app-services (`INotificationsAppService`, `IPriceAlertsAppService`), `SseDispatcher` singleton, HttpClients (social/gateway), три hosted services (`KafkaConsumerService`, `PriceDriftWatcherService`, `PriceAlertEvaluatorService`), JWT bearer, Swagger, health checks
- `Extensions/MigrationExtensions.cs` — `MigrateAndSeedAsync`: `MigrateAsync` + `RequiredTables` whitelist (`notifications`, `notification_settings`, `push_subscriptions`, `price_alerts`); пустая схема → recreate from model, partial → hard-fail
- `Middleware/GlobalExceptionMiddleware.cs` — единый обработчик исключений
- `appsettings*.json` — конфигурация окружений

---

## tests/

Тестов **нет** — код является единственным источником истины.

---

## Что считать изменением структуры

- новые/изменённые HTTP endpoints, SSE-контракт или Web Push contract (`push/*`)
- новые notification kinds или изменение `PushAsync` fan-out (opt-out / dedup / persist / SSE / Web Push)
- изменение consumed Kafka topics или формата envelope (`type`/`payload`)
- изменение схемы таблиц `notifications` / `notification_settings` / `push_subscriptions` / `price_alerts`, индексов, миграций
- изменение price-watcher логики, источников символов или порогов
- изменение price-alert evaluator логики (cadence, fire-once/re-arm, источник цен) или `/api/alerts` контракта
- изменение Web Push sender (payload, dead-sub cleanup, VAPID config) или `PushSettings`
- изменение config-секций, health-flow или зависимостей (social/gateway)
