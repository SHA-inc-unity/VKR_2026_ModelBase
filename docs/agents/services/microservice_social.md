# microservice_social

## Что это

Social-сервис на .NET 8 (Clean Architecture), владелец `/api/social/*`: favorites, threaded
comments + likes и per-coin sentiment (bullish/bearish голосование).

Собственный PostgreSQL (EF Core, snake_case). Сидит на REST+JWT-плоскости: валидирует shared HS256 JWT
(issuer `account-service`, audience `exchange-app`), а клиент ходит сюда **через gateway**
(`SocialController`, BFF-forward), не напрямую. **Produces** Kafka `events.social.v1`
(`comment.created`/`comment.liked`/`favorite.added`/`favorite.removed`), который потребляет
`microservice_notification`. Service-to-service `/internal/*` (author by comment, users by symbol,
все избранные символы) защищён `X-Internal-Api-Key`, а не JWT.

Контейнер `social_service_api` (порт 7530→5000). Модель данных: `favorites` (`{user_id,symbol}`),
`comments` (threaded через `parent_id`, soft-delete), `comment_likes` (`{comment_id,user_id}` = дедуп),
`asset_sentiment` (`{user_id,target_type,target_id}` = один голос на target, GROUP BY counts,
persistent-until-changed). Миграции авто-применяются на старте; `RequiredTables` whitelist должен
содержать каждую таблицу, иначе boot падает. `TreatWarningsAsErrors=true`; тестов нет; .NET SDK на
admin-хосте отсутствует — сборка на backend-хосте или в Docker.

## Что читать перед кодом

- [../../../microservice_social/README.md](../../../microservice_social/README.md)
- [../../../microservice_social/STRUCTURE.md](../../../microservice_social/STRUCTURE.md)
- [../WORKFLOW.md](../WORKFLOW.md)

## Что обновлять после кода

- `microservice_social/README.md`
- `microservice_social/STRUCTURE.md`
- [../CHANGE_LOG.md](../CHANGE_LOG.md)

## Когда обязательно обновлять Markdown

- изменения `/api/social/*` или `/internal/*` маршрутов, auth-требований и JWT
- изменения модели данных, миграций и `RequiredTables` whitelist
- изменения Kafka-контракта `events.social.v1` и типов событий
- изменения интеграции с Account (`/internal/users/{id}`) и deployment-конфигурации
