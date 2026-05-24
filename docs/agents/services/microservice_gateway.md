# microservice_gateway

## Что это

Mobile BFF gateway для внешних HTTP-запросов.

`/api/admin/*` — server-to-server admin facade, защищённый Account Service JWT с ролью `admin`; legacy static-key auth не используется.
Фасад теперь включает и dedicated `market-watcher/*` routes для split-mode страницы `microservice_admin` `/market-watcher`.
Отдельно gateway держит lightweight frontend-contract surface для mobile/web: auth passthrough `POST /api/account/{register,login,refresh,logout}`, public `GET /api/v1/market/{overview,tickers,trending,top-movers,quotes/realtime,convert,converter/quote,config,chart}`, `POST /api/v1/market/quotes/batch`, `GET /api/news/home`, personal `GET /api/portfolio/summary`, `/api/exchanges/*`, `/api/alerts/*`, `/api/services/toggles` и `GET /api/admin/{summary,users,services,statistics}`. Personal fallback state больше не strictly process-local: он хранится через `IDistributedCache` и становится shared/durable при настроенном Redis. Chart route дополнительно использует layered hot cache, cross-limit reuse, bounded sync hydrate+reread и weak `ETag`/`304` semantics; `pending` там теперь означает только уже занятый ingest-lock, still-running queued ingest after wait budget или `claim_check`, а downstream `latest_rows` failures/timeouts поднимаются как `503`, поэтому при изменении polling contract, cache-policy или response validators Markdown нужно синхронизировать особенно внимательно.

## Что читать перед кодом

- [../../../microservice_gateway/README.md](../../../microservice_gateway/README.md)
- [../../../microservice_gateway/API.md](../../../microservice_gateway/API.md)
- [../../../microservice_gateway/STRUCTURE.md](../../../microservice_gateway/STRUCTURE.md)
- [../WORKFLOW.md](../WORKFLOW.md)

## Что обновлять после кода

- `microservice_gateway/README.md`
- `microservice_gateway/API.md`
- `microservice_gateway/STRUCTURE.md`
- [../CHANGE_LOG.md](../CHANGE_LOG.md)

## Когда обязательно обновлять Markdown

- изменения маршрутизации, downstream integration, auth passthrough/admin-role authorization, market snapshot contracts, state persistence semantics, config и health-flow
