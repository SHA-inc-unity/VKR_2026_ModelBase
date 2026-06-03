# microservice_notification

## Что это

.NET 8 Clean Architecture сервис с собственной PostgreSQL: per-user notification
inbox + real-time SSE delivery + price-drift watcher. Контейнер
`notification_service_api` (`7550 → 5000`).

**Consume-only** по Kafka: подписан на `events.social.v1` и `events.news.v1`
(`redpanda:29092`) и превращает события в notifications. Клиент ходит сюда только
через gateway (`NotificationsController` форвардит `/api/notifications/*` и
`/api/notification-settings`, включая SSE-stream).

Delivery — inbox (persisted) + SSE-stream `GET /api/notifications/stream?access_token=<jwt>`
(in-memory `SseDispatcher`, достаёт только подключённых клиентов; при закрытой
вкладке/приложении SSE-доставки нет — это gap, который закроют push-уведомления).
Единственная точка fan-out — `NotificationsAppService.PushAsync` (opt-out → dedup
→ persist → SSE); всё новое (web-push) подключается там.

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

- изменения HTTP endpoints или SSE-контракта
- новые notification kinds или изменение `PushAsync` fan-out (opt-out / dedup / persist / push)
- изменения consumed Kafka topics или формата envelope (`type`/`payload`)
- изменения price-watcher логики, источников символов или порогов
- изменения схемы БД, миграций, config-секций или health-flow
