# API Gateway — Mobile BFF

ASP.NET Core 8 API Gateway (Backend for Frontend) for the Exchange App Flutter client.

The gateway is the **single entry point** for the mobile app. It aggregates responses from multiple downstream services, handles authentication, and returns partial/degraded responses when a service is unavailable.

Downstream IPC is **Kafka-only** (Redpanda broker). The former HTTP client to
`microservice_account` was replaced with `KafkaRequestClient` (async
request/reply on a per-instance `reply.gateway.{instanceId}` inbox).
Gateway bootstrap-ит этот reply-inbox topic через Kafka Admin API в
background-loop и считает Kafka request/reply path ready только после
реального consumer assignment на этот inbox. Если Kafka временно недоступна
или controller/leader ещё не поднялся, процесс gateway не падает и `/health`
остаётся доступен. Если Kafka Admin create не успел подтвердить topic в
пределах startup budget, gateway дополнительно bootstrap-ит reply inbox через
producer publish в сам `reply.gateway.{instanceId}` и продолжает retry-loop,
пока topic/assignment не поднимутся. Это убирает ложное состояние
`reply inbox ready`, при котором admin facade успевал отправить request, но
симметрично зависал на timeout по всем Kafka-backed admin route-ам.
Даже когда `CreateTopicsAsync` проходит штатно, gateway теперь сразу пишет
в новый inbox bootstrap-marker. Это двигает HighWatermark выше `0` ещё до
первого реального reply и не даёт `redpanda-janitor` принять живой, но пока
idle `reply.gateway.*` topic за пустой orphan и удалить его с последующим
`partitions revoked -> 503` на admin facade.
Readiness для Kafka вынесена в отдельный `GET /health/ready`: он проверяет
не только metadata lookup по `Kafka:BootstrapServers`, но и фактическую
готовность request/reply path — у gateway должен быть назначен consumer на
`reply.gateway.{instanceId}`. Docker healthcheck gateway и admin split/local
health probe теперь смотрят именно в `/health/ready`, поэтому состояние
"HTTP процесс жив, но bootstrap или reply inbox path мёртв" больше не
маскируется как healthy. Сам `/health/ready` теперь возвращает JSON с
per-check status/description, поэтому обычный `curl` по backend-host сразу
показывает, это broker bootstrap или reply inbox readiness.
Для live-диагностики gateway пишет связку логов `AdminFacade request ...`
и `KafkaRequest ...` с `topic`, HTTP path, `replyInbox`, duration,
timeout и `correlationId`; payload и bearer token не логируются. Для
reply-inbox path дополнительно сохраняется последний readiness state
(`ReplyInboxStatus`), который попадает и в `/health/ready`, и в detail
structured `504`, если request fast-fail'ится до отправки в Kafka.

Для `/api/admin/*` transport-failures на Kafka publish-path больше не падают
в raw `500` через global exception middleware. Если gateway не может отправить
Kafka request из-за broker/connectivity проблемы, admin facade возвращает
structured `503` с `code=admin_kafka_unavailable`; timeout ожидания reply
возвращается как structured `504` с `code=admin_kafka_timeout`. Если reply
inbox ещё не ready, gateway не ждёт весь route timeout: readiness wait теперь
обрезается коротким budget, чтобы split admin получал быстрый backend `504`
с последним state, а не generic client-side HTTP timeout.

Browser-facing routes теперь получают CORS policy прямо на gateway: public и
protected mobile/web endpoint-ы (`/api/*`, кроме `/api/admin/*`) отвечают
`Access-Control-Allow-*`, а preflight `OPTIONS` для routes с `Authorization`
обрабатывается до JWT auth, чтобы web-клиенты не падали на `405`/browser-side
`Failed to fetch`. Admin facade намеренно помечен `DisableCors`: он рассчитан
на server-to-server use из `microservice_admin`, а не на browser cross-origin.

Admin facade теперь включает и dedicated split-mode surface для страницы
`Market Watcher`: `POST /api/admin/market-watcher/{status,set-enabled,rows,logs}`
проксируют соответствующие `cmd.data.market_watcher.*` команды, поэтому
`microservice_admin` может управлять watcher-ом через backend-host без прямого
Kafka доступа.

---

## Agent Documentation

- [API.md](API.md) — frontend-oriented API reference: вход, выход, ошибки, degraded/pending semantics
- [STRUCTURE.md](STRUCTURE.md) — file/module map of the gateway
- [../docs/agents/services/microservice_gateway.md](../docs/agents/services/microservice_gateway.md) — service profile for docs-first work
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — shared repository workflow for agents

---

## Architecture

```text
Flutter App
    │
    ▼
API Gateway  :7520 (host) -> :5020 (container)
    ├── GET /api/app/bootstrap    ← aggregates: Account (optional)
    ├── POST /api/account/*       ← proxies:    Account Service (HTTP auth flow)
    ├── GET /api/account/me       ← proxies:    Account Service (via Kafka)
    ├── GET /api/dashboard        ← aggregates: Guest → Market + News; User → Portfolio + Market + News
  ├── GET /api/v1/market/overview ← snapshot-backed public market overview
  ├── GET /api/v1/market/tickers  ← list-ready market snapshot cards
  ├── GET /api/v1/market/trending, /api/v1/market/top-movers ← backend-owned home feeds
  ├── POST /api/v1/market/quotes/batch ← lightweight quote refresh by symbol set
  ├── GET /api/v1/market/convert ← frontend-friendly convert alias (`from/to/sourceLabel`)
  ├── GET /api/v1/market/converter/quote ← asset conversion quote from snapshot prices
    ├── GET /api/v1/market/config ← Market config (symbols, timeframes, candle grids)
    ├── GET /api/v1/market/chart  ← OHLCV candles (Redis cached, Kafka ingest)
    ├── GET /api/portfolio/summary ← gateway-local frontend contract state
    ├── /api/exchanges/*, /api/alerts/*, /api/services/toggles
    │                              ← gateway-local frontend contract state
  ├── GET /api/news, /api/news/home ← sorted public news feed fallback
    ├── GET /api/notifications    ← gateway-local empty inbox fallback
    └── GET /api/admin/{summary,users,services,statistics}
                                   ← lightweight mobile-admin surface (JWT admin)
    │
    ├── Account Service        (HTTP auth proxy + Kafka: cmd.account.*)
    ├── Market/Bybit           (HTTP: api.bybit.com, Redis cache, Kafka ingest)
  └── Frontend fallback state (distributed cache-backed; Redis shared when configured)
```

Gateway now also exposes a lightweight frontend-contract layer for routes that do not yet have a dedicated owner service. These payloads are stable enough for mobile/web integration. Linked exchanges, alerts, service toggles and lightweight mobile-admin counters are now stored through `IDistributedCache`: with Redis configured they survive restart and can be shared across gateway instances; without Redis the fallback distributed-memory cache keeps the old per-process semantics.

---

## Endpoints

| Method | Path | Auth | Description |
| ------ | ---- | ---- | ----------- |
| GET | `/api/app/bootstrap` | Optional | One-shot app init — user, feature flags, system status |
| POST | `/api/account/register` | None | Register through the gateway auth proxy |
| POST | `/api/account/login` | None | Login through the gateway auth proxy; accepts `email` or `login` |
| POST | `/api/account/refresh` | None | Refresh tokens through the gateway auth proxy |
| POST | `/api/account/logout` | Required | Logout through the gateway auth proxy |
| GET | `/api/account/me` | Required | Current user profile |
| GET | `/api/dashboard` | Optional | Aggregated main screen data; guest gets only public sections, user also gets personal sections |
| GET | `/api/v1/market/overview` | None | Public market overview for the home screen |
| GET | `/api/v1/market/tickers` | None | Searchable, sortable, paginated market snapshot list with optional `collection` and `snapshotId` |
| GET | `/api/v1/market/trending` | None | Dedicated backend feed for home Trending cards |
| GET | `/api/v1/market/top-movers` | None | Dedicated backend feed for home Top movers cards |
| POST | `/api/v1/market/quotes/batch` | None | Lightweight quote refresh for a symbol set |
| GET | `/api/v1/market/convert` | None | Frontend-compatible converter alias using `from/to/sourceLabel` |
| GET | `/api/v1/market/converter/quote` | None | Asset conversion quote derived from snapshot prices |
| GET | `/api/v1/market/config` | None | Symbols, timeframes, candle-count grids, defaults |
| GET | `/api/v1/market/chart` | None | OHLCV candles — `?symbol=BTCUSDT&timeframe=5m&limit=200` |
| GET | `/api/portfolio/summary` | Required | Detailed portfolio summary (gateway-owned fallback state, distributed-cache-backed when Redis is configured) |
| GET/POST/PATCH/DELETE | `/api/exchanges/*` | Required | Available exchanges + linked exchanges CRUD |
| GET/POST/PATCH/DELETE | `/api/alerts*` | Required | Price alerts CRUD |
| GET/PATCH | `/api/services/toggles` | Required | Service toggles for settings UI |
| GET | `/api/news` | None | Latest news items |
| GET | `/api/news/home` | None | Compact home-screen news feed with optional `limit` and `tag` |
| GET | `/api/notifications` | Required | User notifications |
| GET | `/api/admin/{summary,users,services,statistics}` | Admin JWT | Lightweight mobile-admin surface |
| GET | `/health` | None | Health check |
| GET | `/health/ready` | None | Readiness check incl. Kafka bootstrap and reply inbox assignment; returns JSON diagnostics with per-check descriptions |
| GET | `/swagger` | None (dev only) | Swagger UI |

All responses include `X-Correlation-Id` header.

Auth error note:

- `GET /api/account/me` and auth proxy failures now use the shared JSON `ErrorResponse` contract on gateway-managed errors;
- successful auth proxy responses still pass through the downstream JSON payload unchanged.

Browser/web note:

- gateway теперь сам отвечает CORS headers для public/protected client routes;
- JWT-protected routes вроде `/api/account/me` и `/api/notifications` корректно проходят browser preflight `OPTIONS` до auth-check;
- `/api/admin/*` не предназначен для browser CORS и остаётся server-to-server surface.

Health flow:

- `/health` = liveness of the ASP.NET process
- `/health/ready` = readiness of gateway request/reply path, including Kafka bootstrap reachability and reply inbox assignment
- Docker Compose healthcheck uses `/health/ready`

Полный контракт для frontend-интеграции, включая примеры запросов/ответов и правила обработки degraded/pending состояний, вынесен в [API.md](API.md).

---

## Authentication Flow

1. Client may call `POST /api/account/login` / `register` / `refresh` / `logout` on the gateway; these routes proxy the same JSON contract to Account Service.
2. Account Service returns auth payload with tokens plus top-level `uid`, `id`, `email`, `accountType`, `roles`.
3. Client passes `Authorization: Bearer <accessToken>` on all gateway requests.
4. Gateway validates the JWT using the shared `SecretKey` (same as Account Service).
5. Gateway extracts the `sub` / `nameid` claim from the already-validated JWT
   and sends `{ user_id }` over Kafka (`cmd.account.get_user`) instead of
   forwarding the raw bearer downstream.

Для mobile API у gateway сейчас два фактических access-mode:

- `guest` = анонимный вызов без JWT. Это не отдельная persisted role в `microservice_account`, а именно отсутствие токена на gateway.
- `user` = валидный Bearer JWT с пользовательскими claims.

С практической точки зрения guest разрешён на `bootstrap`, `dashboard`, `market/*` и `news`, а personal routes (`account/me`, `notifications`) остаются под JWT.

---

## Graceful Degradation

The gateway **never returns 500 for downstream failures**. Instead:

- Aggregated endpoints return partial responses.
- Failed sections are listed in `degradedServices` (bootstrap) or `meta.degradedSections` (dashboard).
- Client renders what it has and shows a warning for degraded sections.

---

## Environment Variables

| Variable | Required | Default | Description |
| -------- | -------- | ------- | ----------- |
| `JWT_SECRET_KEY` | Yes | — | Shared HMAC secret (≥32 chars, same as Account Service) |
| `JWT_ISSUER` | No | `account-service` | JWT issuer claim; must match Account Service |
| `JWT_AUDIENCE` | No | `exchange-app` | JWT audience claim; must match Account Service |
| `KAFKA_BOOTSTRAP_SERVERS` | No | `redpanda:29092` | Kafka bootstrap (Redpanda) |
| `ACCOUNT_SERVICE_URL` | No | `http://account-api:5000` | Base URL for gateway HTTP auth proxy routes |
| `ACCOUNT_URL` | No | — | Legacy alias for `ACCOUNT_SERVICE_URL`; used if the primary variable is absent |
| `Cors__AllowAnyOrigin` | No | `true` | Browser-facing CORS mode for gateway routes except `/api/admin/*`. `true` enables `AllowAnyOrigin`; set to `false` to use explicit origins from `Cors__AllowedOrigins__*`. |
| `Cors__AllowedOrigins__0` ... | No | — | Explicit allowed origins when `Cors__AllowAnyOrigin=false`, e.g. `https://sha-trade.tech`, `https://www.sha-trade.tech`. |
| `Cors__PreflightMaxAgeSeconds` | No | `600` | Browser preflight cache TTL for gateway CORS responses. |

Copy `.env.example` → `.env` and fill in the values.

---

## Quick Start

### Local (dotnet run)

```bash
cp .env.example .env
# Edit .env to set JWT_SECRET_KEY

cd src/GatewayService.API
dotnet run
# Swagger: http://localhost:5020/swagger
```

### Docker Compose

```bash
cp .env.example .env
docker compose up --build
# Gateway:  http://localhost:7520
# Account:  http://localhost:7510
```

---

## Market Chart — Request Queue & Coalescing

All `GET /api/v1/market/chart` requests pass through `ChartRequestQueue` — a singleton decorator in front of `ChartService`.

**Key behaviours:**

| Behaviour | Detail |
| --------- | ------ |
| **Coalescing** | Identical `(symbol, timeframe, limit)` requests that arrive while one is already in-flight share a single downstream call. Only the creator talks to the data service; all other callers wait on the same `TaskCompletionSource`. |
| **CT isolation** | Each caller passes its own `CancellationToken`. Cancelling one waiter does not affect the creator or other waiters (`WaitAsync(callerCt)` is per-waiter; the creator's `workCts` is independent). |
| **Hot-window cache** | Latest chart windows are cached by `(symbol, timeframe, limit)`, so repeated requests like `ETHUSDT + 15m + 100` normally return from Redis/memory cache before queue/coalescing allocates new work. |
| **Sync lazy hydrate** | For a known symbol/timeframe with a missing or incomplete local window, `ChartService` submits an `ingest` dataset job via `cmd.data.dataset.jobs.start` and waits on `cmd.data.dataset.jobs.get` until terminal status before re-reading rows in the same HTTP request. This reuses the data-service queue with 4 ingest slots instead of bypassing it. |
| **Per-table ingest serialization** | Because the hydrate path now goes through `dataset_jobs`, two concurrent chart hydrates for the same dataset table cannot execute together: `DatasetJobRunner` keeps the global ingest cap at 4 and serializes active jobs by `target_table`. |
| **Ingest error cooldown** | If a synchronous or background ingest fails, the ingest lock is replaced with a short error cooldown, so the next request can retry after `IngestErrorCooldownSeconds` instead of waiting for the full lock TTL. |
| **Claim-check detection** | `DataServiceClient` distinguishes `claim_check` from an empty rows result, so `ChartService` does not trigger a false ingest retry. Direct claim-check fetch is not implemented yet; the client currently gets a retry/pending scenario and should reduce `limit` if needed. |
| **Window-scoped coverage** | `ChartService` computes coverage over `limit × IngestWindowMultiplier` candles (not the full table) to avoid a permanently-full coverage flag on large datasets. |

**Queue settings** (in `appsettings.json` / `MarketSettings`):

| Key | Default | Description |
| --- | ------- | ----------- |
| `QueueTotalConcurrency` | `10` | Max simultaneous downstream calls |
| `QueueHeavyConcurrency` | `3` | Reserved capacity for "heavy" timeframes |
| `QueueMaxWaitSeconds` | `5` | Hard timeout waiting for the semaphore |
| `IngestErrorCooldownSeconds` | `30` | How long to pause after an ingest error |

---

## Deployment Automation

Root-level scripts in [../deploy/](../deploy/) automate gateway deployment and reconciliation.

| Script | Purpose |
| ------ | ------- |
| `../deploy/modelline-deploy.yml` | deployment config for image rollout / reconciliation; targets real compose services (`infra: redpanda/redpanda-console/minio/nginx/redpanda-janitor`, `gateway: gateway-service`, `data: data`, `analytic: api`, `account: account-api`) so backend-host reconcile can actually roll out gateway/nginx fixes |
| `../deploy/reconcile.ps1` | Windows reconcile script |
| `../deploy/reconcile.sh` | Linux/macOS reconcile script; parser fixed to handle multi-service `modelline-deploy.yml` under `set -e` without aborting on the second entry |
| `../deploy/status.ps1` | runtime status helper |

---

## Running Tests

```bash
# Unit tests
dotnet test tests/GatewayService.UnitTests

# Integration tests (in-process, no live services required)
dotnet test tests/GatewayService.IntegrationTests

# Contract tests
dotnet test tests/GatewayService.ContractTests

# Smoke tests
dotnet test tests/GatewayService.SmokeTests

# All
dotnet test
```

---

## Project Structure

```text
API.md               — frontend-oriented HTTP contract reference
src/
  GatewayService.API/
    Aggregators/         — BFF orchestration logic (Bootstrap, Dashboard)
    Clients/             — Downstream clients (Account via Kafka + HTTP auth proxy, snapshot-backed market client, plus news/notifications/portfolio fallbacks)
    Controllers/         — Thin ASP.NET controllers, including mobile BFF routes for account auth, portfolio, exchanges, alerts, toggles and mobile-admin summaries
    DTOs/                — Response contracts, ErrorResponse, market snapshot DTOs and frontend contract request/response models
    Extensions/          — ServiceCollectionExtensions
    Frontend/            — Distributed-cache-backed frontend contract state used by fallback mobile routes
    Kafka/               — KafkaSettings, Topics, KafkaRequestClient (request/reply)
    Market/              — Full market API: TimeframeMap, CandleCountGrid, MarketSettings,
                           ChartService, ChartRequestQueue (coalescing), MarketCacheService,
                           DataServiceClient (claim-check detection), BybitClient, MarketIngestService,
                           MarketConfigService, IChartService, ServiceResult<T>
    Middleware/          — CorrelationId, GlobalException
    Settings/            — Strongly typed config sections
../deploy/
  modelline-deploy.yml  — Root-level deployment config with real compose service names for infra/gateway/data/analytic/account
  reconcile.ps1 / .sh   — Root-level reconcile scripts; infra reconcile restarts backend nginx/redpanda containers explicitly
  status.ps1            — Root-level container status helper
tests/
  GatewayService.UnitTests/
  GatewayService.IntegrationTests/    — includes MarketQueueIntegrationTests
  GatewayService.ContractTests/
  GatewayService.SmokeTests/
```

---

## QA Checklist

- [ ] `GET /health` → 200
- [ ] `GET /api/app/bootstrap` (no token) → 200, `user` is null, `degradedServices` is empty or contains only real downstream issues
- [ ] `GET /api/app/bootstrap` (valid JWT) → 200, `user.email` populated
- [ ] `POST /api/account/login` → 200, accepts `email` or `login`, returns top-level `uid`, `id`, `email`, `roles`
- [ ] `GET /api/account/me` (no token) → 401 JSON with `status`, `title`, `timestamp`
- [ ] `GET /api/dashboard` (no token) → 200 guest payload, `portfolio = null`, `meta.degradedSections` не содержит `portfolio`
- [ ] `GET /api/v1/market/overview` → 200 public payload with snapshot-derived metrics, `meta.generatedAt`, `meta.updatedAt`
- [ ] `GET /api/v1/market/tickers?page=1&pageSize=25` → 200, `items`, `total`, `meta.updatedAt`
- [ ] `POST /api/v1/market/quotes/batch` → 200, returns `items` and `missingSymbols`
- [ ] `GET /api/v1/market/trending?limit=5` and `GET /api/v1/market/top-movers?limit=5` → 200 feed payloads with same ticker card shape
- [ ] `GET /api/v1/market/convert?from=BTC&to=USDT&amount=1` → 200 quote payload with `sourceLabel`
- [ ] `GET /api/v1/market/converter/quote?fromAsset=BTC&toAsset=USDT&amount=1` → 200 legacy-compatible quote payload
- [ ] `GET /api/news` / `GET /api/news/home?limit=3&tag=market` → 200, newest-first items, `degraded` reflects only real news client failure
- [ ] All responses contain `X-Correlation-Id` header
- [ ] Swagger UI accessible at `/swagger` in Development

---

## Dependencies

| Package | Version | Purpose |
| ------- | ------- | ------- |
| `Microsoft.AspNetCore.Authentication.JwtBearer` | 8.* | JWT validation |
| `Confluent.Kafka` | 2.* | Kafka producer/consumer for request/reply to downstream services |
| `Serilog.AspNetCore` | 8.* | Structured logging |
| `Swashbuckle.AspNetCore` | 6.* | Swagger/OpenAPI |
