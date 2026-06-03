# microservice_gateway

## Что это

Mobile BFF gateway для внешних HTTP-запросов.

`/api/admin/*` — server-to-server admin facade, защищённый Account Service JWT с ролью `admin`; legacy static-key auth не используется.
Фасад теперь включает и dedicated `market-watcher/*` routes для split-mode страницы `microservice_admin` `/market-watcher`.
Также фасад держит `GET /api/admin/events` — SSE-релей backend `events.*` (EVT_*) для split-mode admin: `AdminEventRelayHub` (hosted service) держит один Kafka-consumer на все `AdminTopics.AllEvents` и fan-out'ит их подключённым SSE-клиентам; это единственный `GET` среди `/api/admin/*`. Никакой Redpanda-креденшл не выходит за backend-хост — admin reverse-проксирует поток под обычным admin-JWT.
Отдельно gateway держит lightweight frontend-contract surface для mobile/web: auth passthrough `POST /api/account/{register,login,refresh,logout}`, public `GET /api/v1/market/{overview,tickers,categories,trending,top-movers,gainers,losers,quotes/realtime,convert,converter/quote,config,chart}`, `POST /api/v1/market/quotes/batch`, `GET /api/news/home`, personal `GET /api/portfolio/summary`, `/api/exchanges/*`, `/api/alerts/*`, `/api/services/toggles` и `GET /api/admin/{summary,users,services,statistics}`. `SocialController` проксирует Social Service: favorites/comments/likes и per-coin sentiment (`GET /api/social/sentiment` anonymous+query → `myVote`; `POST /api/social/sentiment` authorized+body) через общий `Forward(method,path,requireBearer,query,body)` без отдельного клиента. Personal fallback state больше не strictly process-local: он хранится через `IDistributedCache` и становится shared/durable при настроенном Redis. Chart route дополнительно использует layered hot cache, cross-limit reuse, bounded sync hydrate+reread и weak `ETag`/`304` semantics; `pending` там теперь означает только уже занятый ingest-lock, still-running queued ingest after wait budget или `claim_check`, а downstream `latest_rows` failures/timeouts поднимаются как `503`, поэтому при изменении polling contract, cache-policy или response validators Markdown нужно синхронизировать особенно внимательно. `tickers`/`trending`/`top-movers` отдают **реальную** market cap (`circulatingSupply × livePrice`) + `circulatingSupply`/`totalSupply`/`maxSupply`/`fdv`/`ath`: supply/ATH берутся из CoinGecko `/coins/markets` через `CoinMetadataService` (лениво кэшируется ~6 ч, soft-fail к пустой карте) по curated `CoinGeckoIdMap` (`base → coingecko_id`, collision-safe), live price — из 30-сек Bybit snapshot; непокрытые базы/CoinGecko-miss → `null` cap (graceful degrade, без отката к старому OI proxy), сортировка по `marketCap` ставит их null-last. Те же ticker payload-ы несут multi-window price-change % `change1h`/`change7d`/`change30d` (все nullable), посчитанные **из нашего собственного candle store** (microservice_data) через `cmd.data.dataset.latest_rows` сервисом `MarketWindowChangeService` (лениво кэшируется на `WindowChangeCacheTtlSeconds`≈120с, soft-fail к пустой карте, cache-warm = ноль Kafka-вызовов) — **без** изменения data-service, нового topic или external API; 24h % (`change24h`) по-прежнему из Bybit snapshot. Окно приходит `null`, если у монеты нет свечей, достаточно старых для него. Те же ticker payload-ы несут `categories[]` — curated sector slug-и из нашей **собственной** static-карты `CoinCategoryMap` (`base → slugs`, зеркалит `CoinGeckoIdMap`; **без** external/CoinGecko вызова, topic или правок data-service): canonical список из 12 секторов (`layer1, layer2, defi, ai, meme, rwa, staking, solana, exchange, stable, gaming, oracle`), `0..N` слогов на базу (~86 из 92 баз покрыто, остальные — пустой `[]`/All-only). `GET /api/v1/market/tickers?category=<slug>` делает server-side sector-фильтр (после collection-фильтра, до sort/paging), а `GET /api/v1/market/categories` отдаёт canonical список + live count tracked-тикеров per slug из snapshot.

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
