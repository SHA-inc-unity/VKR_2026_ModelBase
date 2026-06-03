# microservice_gateway — Структура

> Обновляй этот файл при изменении BFF-маршрутов, Kafka-клиентов, агрегаторов или состава тестов.

---

## Связанная документация

- [README.md](README.md) — runbook, endpoint-ы и текущая архитектура gateway
- [API.md](API.md) — frontend-oriented HTTP reference с примерами входа/выхода и правилами интеграции
- [../docs/agents/services/microservice_gateway.md](../docs/agents/services/microservice_gateway.md) — агентный профиль сервиса
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first workflow

---

## Корень сервиса

| Файл | Описание |
| ---- | -------- |
| `GatewayService.sln` | Solution-файл .NET |
| `Dockerfile` | Контейнеризация gateway |
| `docker-compose.yml` | Локальный compose-стек gateway; container healthcheck смотрит `GET /health/ready`, а не liveness-only `/health` |
| `global.json` | Привязка .NET SDK |
| `README.md` | Основная документация сервиса |
| `API.md` | Подробная HTTP-спецификация для frontend: headers, auth, вход, выход, ошибки, degraded/pending semantics |
| `STRUCTURE.md` | Этот файл |

---

## src/GatewayService.API/

| Папка / файл | Назначение |
| ------------ | ---------- |
| `Program.cs` | bootstrap приложения, DI, middleware pipeline, browser-facing CORS policy (`UseCors`) и health routing (`/health` liveness, `/health/ready` readiness). Readiness теперь требует и Kafka bootstrap, и живой reply-inbox path через `KafkaRequestReplyHealthCheck` |
| `Aggregators/` | BFF-оркестрация составных экранов и bootstrap-ответов; `DashboardAggregator` guest-aware и не пытается собирать `portfolio`, если запрос пришёл без user identity |
| `Clients/` | Downstream clients, включая Kafka request/reply к account service, HTTP auth proxy `AccountAuthProxyClient`, snapshot-backed `MarketServiceClient` поверх Bybit tickers (с watcher-backed realtime quote overlay через `cmd.data.market_watcher.rows` и реальной supply-based market cap из CoinGecko `/coins/markets` поверх snapshot price), и gateway-local fallback clients для `news/notifications/portfolio` |
| `Controllers/` | HTTP endpoints gateway; `AccountController` держит auth proxy routes `register/login/refresh/logout` и нормализует gateway-managed auth/profile failures в `ErrorResponse`, `DashboardController` работает в optional-auth режиме, а `MarketController` теперь публикует `overview`, `tickers` (с опциональным server-side `?category=<slug>` sector-фильтром), dedicated feeds `trending` / `top-movers` / `gainers` / `losers`, `categories` (curated sector list + live count per slug), `quotes/batch`, watcher-backed `quotes/realtime`, legacy `converter/quote` и frontend-compatible `convert`. `NewsController` поддерживает server-side `limit` / `tag` filtering для `/api/news` и `/api/news/home`. `UpdatesController` — public `GET /api/updates` (app updates / changelog): зовёт `IUpdatesService`, который делает Kafka request/reply в data-service (`cmd.data.updates.list`) и проксирует `{ releases: [...] }` verbatim; таймаут/`error`/downstream-сбой → `503 { error: "updates_unavailable" }`. Отдельные `PortfolioController`, `ExchangesController`, `ServiceTogglesController` и `MobileAdminController` остаются lightweight mobile BFF surface. `AlertsController` теперь **не** хранит алерты в gateway, а форвардит `/api/alerts` CRUD (`GET`/`POST`/`PATCH {id}`/`DELETE {id}`) в notification service через тот же `INotificationsHttpProxyClient`, что и `NotificationsController` (raw bearer + body + querystring, downstream-ответ verbatim, недоступность → `503 notification_service_unavailable`). `AdminController` по-прежнему помечен `DisableCors`, потому что `/api/admin/*` server-to-server facade не рассчитан на browser JS. |
| `DTOs/` | Контракты ответов и ошибок; помимо `ErrorResponse` есть `FrontendContractsRequests`, `FrontendContractsResponses` и `MarketSnapshotResponses` для public market overview/list/feed/quotes/realtime/convert payloads, portfolio summary, exchanges, alerts, toggles и mobile-admin payloads |
| `Frontend/` | `IFrontendContractState` и `FrontendContractState`: state для linked exchanges, service toggles и portfolio/admin fallback payloads, хранимый через `IDistributedCache` (Redis shared cache при наличии конфигурации, distributed-memory fallback без Redis). Алерты здесь **больше не хранятся** — они переехали в microservice_notification, а `AlertsController` форвардит их туда. `FrontendAdminSnapshot.AlertsCount` теперь жёстко `0` (TODO: re-source из notification service), shape DTO не менялся |
| `Extensions/` | Регистрация сервисов и инфраструктурных зависимостей; `ServiceCollectionExtensions` теперь поднимает browser CORS policy из `CorsSettings` (`AllowAnyOrigin` или explicit `AllowedOrigins`), включает preflight cache TTL, резолвит base URI account auth proxy из `ACCOUNT_SERVICE_URL` / `ACCOUNT_URL` и регистрирует `IUpdatesService` singleton рядом с Kafka-backed market singletons |
| `Kafka/` | Kafka settings, topics, `IKafkaRequestClient`, `IKafkaRequestClientProbe`, `KafkaBrokerHealthCheck`, `KafkaRequestReplyHealthCheck` и request client; `AdminTopics` — topic-константы admin facade, включая dedicated watcher topics `cmd.data.market_watcher.{status,set_enabled,rows,logs}` для split-mode admin page. `KafkaRequestClient` bootstrap-ит per-instance reply-inbox `reply.gateway.{instanceId}` в background-loop и теперь помечает reply path ready только после реального consumer assignment. Если Kafka Admin create не подтверждается в startup budget, клиент делает fallback bootstrap publish в сам reply topic и продолжает retry-loop до тех пор, пока inbox не будет существовать и назначен consumer-у; это устраняет ложный symmetric `504` по Kafka-backed admin route-ам после старта gateway раньше Redpanda/controller. Даже при успешном `CreateTopicsAsync` клиент теперь seed-ит inbox bootstrap-marker'ом, чтобы HighWatermark сразу ушёл выше `0` и `redpanda-janitor` не удалил живой, но ещё idle reply topic. Дополнительно клиент ведёт `ReplyInboxStatus` — последний readiness state bootstrap/subscribe/assignment path. `KafkaBrokerHealthCheck` проверяет bootstrap listener, а `KafkaRequestReplyHealthCheck` — assignment reply inbox для `/health/ready` и compose healthcheck; `/health/ready` теперь возвращает JSON с per-check descriptions. Если inbox не ready, request fast-fail'ится по короткому readiness budget и возвращает structured `504` раньше client-side HTTP timeout. Runtime diagnostics логируют `KafkaRequest start/produced/success/timeout/failed` с topic, replyInbox, duration, correlationId и last readiness state без payload. |
| `Market/` | Полный market API — см. ниже |
| `Updates/` | `IUpdatesService` / `UpdatesService` (singleton): Kafka-backed fetch app-updates / changelog из microservice_data на topic `cmd.data.updates.list` (пустой payload, таймаут `Market:KafkaTimeoutSeconds`); возвращает reply JSON verbatim (`{ releases: [...] }`) для public `GET /api/updates`, soft-fail на таймаут/`error` |
| `Middleware/` | CorrelationId, exception handling и другие cross-cutting middleware |
| `Settings/` | strongly-typed конфиги; `AdminSettings` — таймауты admin facade, `CorsSettings` — browser-facing CORS policy (`AllowAnyOrigin`, `AllowedOrigins`, `PreflightMaxAgeSeconds`) |
| `Common/` | общие типы и вспомогательные abstractions |
| `appsettings*.json` | конфигурация окружений |

### Market/

| Файл | Назначение |
| ---- | ---------- |
| `IChartService.cs` | интерфейс chart-сервиса |
| `ChartService.cs` | ядро: layered cache → `latest_rows` → bounded sync ingest/reread для missing/incomplete window; умеет reuse-ить bigger cached chart window для smaller `limit`, чтобы не ходить в Kafka/data-service на каждый соседний polling size. Hydrate не шлёт прямой `cmd.data.dataset.ingest`, а создаёт queued ingest-job через `DataServiceClient`, поэтому попадает в data-service ingest queue (cap 4, per-table serialization). При свободном lock сервис старается дождаться queued ingest и reread rows в том же HTTP-request; `pending` остаётся только для уже занятого ingest-lock, still-running ingest after wait budget или `claim_check`, а explicit downstream `latest_rows` / `rows` failures и ingest error-cooldown поднимаются в controller как `503`. |
| `ChartRequestQueue.cs` | coalescing-декоратор: идентичные `(symbol, timeframe, limit)` запросы разделяют один downstream-вызов; каждый caller имеет независимый `CancellationToken`, а fast-path сначала проверяет hot cache до выделения inflight entry |
| `IMarketCacheService.cs` / `MarketCacheService.cs` | layered market cache: short per-instance memory hot cache поверх `IDistributedCache` + stampede protection (`SetIfNotExistsAsync`, `GetOrCreateAsync`) |
| `IMarketConfigService.cs` / `MarketConfigService.cs` | конфиг символов и таймфреймов |
| `IBybitSymbolProvider.cs` / `BybitSymbolProvider.cs` | получение активных символов с Bybit |
| `ICoinMetadataService.cs` / `CoinMetadataService.cs` | per-coin supply/FDV/ATH metadata из CoinGecko `/coins/markets` для curated universe; лениво кэшируется на `CoinMetadataCacheTtlSeconds` (~6 ч) через `IMarketCacheService.GetOrCreateAsync`, soft-fail к пустой карте (зеркалит overview-паттерн). Используется `MarketServiceClient` для реальной cap = `circulatingSupply × livePrice` + `fdv` |
| `CoinGeckoIdMap.cs` | curated static `base → coingecko_id` карта (collision-safe; bare ticker-lookup небезопасен — ETH/PEPE/SOL и т.п. сталкиваются с десятками монет). Покрывает ~86 из 92 tracked-баз; непокрытые базы → `null` metadata (graceful degrade) |
| `CoinCategoryMap.cs` | curated static `base → category-slugs[]` карта (наши **собственные** данные, **без** external/CoinGecko вызова), зеркалит структуру/конвенции `CoinGeckoIdMap.cs`. Canonical список из 12 категорий (`layer1, layer2, defi, ai, meme, rwa, staking, solana, exchange, stable, gaming, oracle`) с neutral-English `displayName` (frontend локализует по slug); каждой базе сопоставлено `0..N` слогов (покрыто ~86 из 92 баз, остальные — All-only graceful degrade). Accessor-хелперы `CategoriesFor(base)` (всегда непустой/empty list, не null) и `Categories`/`AllSlugs`. Используется snapshot-overlay-ем (`BuildTicker`/`BuildFallbackTicker`) для `SnapshotTicker.Categories`, server-side `?category=<slug>`-фильтром в `GetTickersAsync` и `/categories` listing-endpoint-ом (live count per slug из snapshot) |
| `IMarketWindowChangeService.cs` / `MarketWindowChangeService.cs` | multi-window price-change % (`change1h`/`change7d`/`change30d`, все nullable), посчитанные **из нашего собственного candle store** (microservice_data) через существующий Kafka-запрос `cmd.data.dataset.latest_rows` — **без** изменения data-service, **без** нового topic, **без** external API. На symbol fan-out (Task.WhenAll, soft-fail per symbol): ~31 дневной close (таблица `D`, `step_ms=86_400_000`, `limit=31`) для 7d/30d anchor + ~2 часовых close (таблица `60m`, `step_ms=3_600_000`, `limit=2`) для 1h anchor; колонки `[timestamp_utc, close_price]`. `change = (livePrice − closeNAgo)/closeNAgo×100`, где anchor — newest-but-not-after свеча у края окна; нет свечи / пусто / failure / close ≤ 0 → окно `null`. Карта окон лениво кэшируется на `WindowChangeCacheTtlSeconds` (default 120 с) через `IMarketCacheService.GetOrCreateAsync` под JSON-envelope, soft-fail к пустой карте (зеркалит `CoinMetadataService`). Overlay на snapshot tickers идёт после построения цен; cache-warm путь = ноль Kafka-вызовов |
| `IDataServiceClient.cs` / `DataServiceClient.cs` | Kafka-клиент к data-сервису; различает inline rows, empty rows, `claim_check` и explicit downstream failures/timeouts на chart rows path. Queued ingest идёт через `cmd.data.dataset.jobs.start` + polling `cmd.data.dataset.jobs.get`; клиент теперь отдельно различает terminal failure и still-running job after wait budget, чтобы `ChartService` мог вернуть либо `ok/partial`, либо честный `pending`/`503` без ложного empty fallback. |
| `MarketSettings.cs` | strongly-typed конфиг market-блока (queue-поля, `SnapshotCacheTtlSeconds` для public snapshot routes, `LocalHotCacheSeconds` для per-instance market hot cache, `CoinMetadataCacheTtlSeconds` + опциональный `CoinGeckoApiKey` для per-coin supply/FDV/ATH metadata, `WindowChangeCacheTtlSeconds` для 1h/7d/30d window-change карты) |
| `TimeframeMap.cs` | маппинг таймфреймов ID → Bybit interval |
| `CandleCountGrid.cs` | валидация и маппинг количества свечей |
| `DataTopics.cs` | Kafka topic-константы для market/data integrations: chart-path coverage/rows, dataset job control (`jobs.start`, `jobs.get`), watcher-backed realtime rows (`cmd.data.market_watcher.rows`) и app updates / changelog (`cmd.data.updates.list`, используется `Updates/UpdatesService`) |

---

## ../deploy/

| Файл | Назначение |
| ---- | ---------- |
| `modelline-deploy.yml` | Root-level deployment config, который теперь указывает реальные compose service names для infra/gateway/data/analytic/account; gateway deployable unit = `gateway-service`, analytic = `api`, account = `account-api` |
| `reconcile.ps1` | Windows reconcile script для root-level deploy конфигурации |
| `reconcile.sh` | Linux/macOS reconcile script для root-level deploy конфигурации; parser исправлен, чтобы multi-service YAML не падал на втором entry под `set -e` |
| `status.ps1` | статус контейнеров по root-level deploy конфигурации |

---

## tests/

| Папка | Назначение |
| ----- | ---------- |
| `GatewayService.UnitTests/` | юнит-тесты агрегаторов, клиентов и middleware; помимо `DashboardAggregatorTests` теперь есть focused checks для `AccountController` error envelopes и `FrontendContractState` distributed-cache persistence |
| `GatewayService.IntegrationTests/` | интеграционные тесты in-process; `GatewayIntegrationTests` теперь фиксирует и anonymous `GET /api/dashboard` (guest payload без degraded `portfolio`), и browser CORS на `GET /api/news` / preflight `OPTIONS /api/dashboard` |
| `GatewayService.ContractTests/` | контрактные тесты HTTP API |
| `GatewayService.SmokeTests/` | smoke-проверки ключевых сценариев gateway |

---

## Что считать изменением структуры

- новые BFF endpoints или downstream integrations
- изменение состава папок `Aggregators`, `Clients`, `Kafka`, `Middleware`
- изменение тестового контура или типов тестов
- изменение схемы конфигурации и обязательных env/appsettings
