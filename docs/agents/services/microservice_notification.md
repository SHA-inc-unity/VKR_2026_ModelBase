# microservice_notification

## Что это

.NET 8 Clean Architecture сервис с собственной PostgreSQL: per-user notification
inbox + real-time SSE delivery + self-hosted browser Web Push (VAPID, без Firebase)
+ price-drift watcher. Контейнер `notification_service_api` (`7550 → 5000`).

**Consume-only** по Kafka: подписан на `events.social.v1` и `events.news.v1`
(`redpanda:29092`) и превращает события в notifications. Клиент ходит сюда только
через gateway (`NotificationsController` форвардит `/api/notifications/*` и
`/api/notification-settings`, включая SSE-stream).

Delivery — inbox (persisted) + SSE-stream `GET /api/notifications/stream?access_token=<jwt>`
(in-memory `SseDispatcher`, достаёт только подключённых клиентов) + self-hosted
Web Push (VAPID) для доставки при закрытой вкладке/приложении (закрывает SSE-gap;
подписки в нашей таблице `push_subscriptions`, sender на `WebPush` NuGet,
best-effort, dead-sub cleanup на `404`/`410`, disabled при пустом private key).
Единственная точка fan-out — `NotificationsAppService.PushAsync` (opt-out → dedup
→ persist → SSE → Web Push); всё новое подключается там. Push-endpoints:
`GET/POST /api/notifications/push/{public-key,subscribe,unsubscribe}`, VAPID
private key только через host `.env` (`PUSH_VAPID_PRIVATE_KEY`), не коммитится.

Kinds: `comment.reply`, `news.favorite`, `price.favorite` с per-user toggles
(`EnableReply`/`EnableNews`/`EnablePrice` + `PriceThresholdPct`). Price-триггеры
идут из `PriceDriftWatcherService` по FAVORITED-символам с единым `%`-порогом, а
**не** из gateway `/api/alerts` (те строки хранятся, но не оцениваются — известный
gap).

## Что читать перед кодом

- [../../../microservice_notification/README.md](../../../microservice_notification/README.md)
- [../../../microservice_notification/STRUCTURE.md](../../../microservice_notification/STRUCTURE.md)
- [../WORKFLOW.md](../WORKFLOW.md)

## Что обновлять после кода

- `microservice_notification/README.md`
- `microservice_notification/STRUCTURE.md`
- [../CHANGE_LOG.md](../CHANGE_LOG.md)

## Когда обязательно обновлять Markdown

- изменения HTTP endpoints, SSE-контракта или Web Push contract (`push/*`)
- новые notification kinds или изменение `PushAsync` fan-out (opt-out / dedup / persist / SSE / Web Push)
- изменения consumed Kafka topics или формата envelope (`type`/`payload`)
- изменения price-watcher логики, источников символов или порогов
- изменения Web Push sender, VAPID config (`PushSettings`) или таблицы `push_subscriptions`
- изменения схемы БД, миграций, config-секций или health-flow
