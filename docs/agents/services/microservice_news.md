# microservice_news

## Что это

News-сервис на .NET 8 (Clean Architecture, собственный PostgreSQL, контейнер
`news_service_api`, порт `7540→5000`).

Агрегирует крипто-новости из набора публичных RSS-лент (+ опциональный CryptoPanic
JSON, если задан `AuthToken`) в свою БД и отдаёт **публичный read-only фид**. Клиент
ходит к нему через gateway (`api/news` проксируется 1:1). Сервис **продюсит** Kafka
`events.news.v1` (`news.created`), который потребляет `microservice_notification`; сам
не потребляет ничего.

Пайплайн ингеста живёт в hosted-сервисе `CryptoPanicIngesterService` (имя legacy, по
факту RSS-first): RSS/Atom → full-text из `content:encoded`/`atom:content` → extraction
hero-картинки → time-boxed SmartReader-скрейп **только когда лента не дала body или
картинку** → upsert по уникальному `source_url` → эмит события + bounded backfill старых
неполных записей. Политика показа **«нет картинки — нет публикации»** реализована
фильтром в `NewsRepository.ListAsync` (`image_url` непустой) — это правило **отображения**
в фиде, а не drop при ингесте: строки всегда сохраняются и появляются, когда backfill
найдёт картинку. Миграции применяются на старте + guard `RequiredTables`
(`news_articles`).

## Что читать перед кодом

- [../../../microservice_news/README.md](../../../microservice_news/README.md)
- [../../../microservice_news/STRUCTURE.md](../../../microservice_news/STRUCTURE.md)
- [../WORKFLOW.md](../WORKFLOW.md)

## Что обновлять после кода

- `microservice_news/README.md`
- `microservice_news/STRUCTURE.md`
- [../CHANGE_LOG.md](../CHANGE_LOG.md)

## Когда обязательно обновлять Markdown

- изменения RSS-лент, ingest-пайплайна, политики enrichment/backfill и image-policy
- изменения endpoints (`/api/news`, `?symbol=`, пагинация) и формы DTO/ответов
- изменения структуры таблицы `news_articles`, миграций и `RequiredTables`
- изменения Kafka-контракта `events.news.v1` (`news.created`)
- изменения конфигурации/деплоя (env, порты, контейнер, healthcheck)
