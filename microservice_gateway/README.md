# API Gateway — Mobile BFF

ASP.NET Core 8 API Gateway (Backend for Frontend) for the Exchange App Flutter client.

The gateway is the **single entry point** for the mobile app. It aggregates responses from multiple downstream services, handles authentication, and returns partial/degraded responses when a service is unavailable.

Downstream IPC is **Kafka-only** (Redpanda broker). The former HTTP client to
`microservice_account` was replaced with `KafkaRequestClient` (async
request/reply on a per-instance `reply.gateway.{instanceId}` inbox).
Gateway bootstrap-ит этот reply-inbox topic через Kafka Admin API в
background-loop и подписывается на него сразу после готовности. Если Kafka
временно недоступна или controller/leader ещё не поднялся, процесс gateway
не падает и `/health` остаётся доступен. Если Kafka Admin create не успел
подтвердить topic в пределах startup budget, gateway всё равно продолжает с
best-effort subscribe на reply inbox вместо бесконечной блокировки всех
Kafka-facing запросов состоянием `reply inbox not ready`.
Для live-диагностики gateway пишет связку логов `AdminFacade request ...`
и `KafkaRequest ...` с `topic`, HTTP path, `replyInbox`, duration,
timeout и `correlationId`; payload и shared token не логируются.

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
    ├── GET /api/account/me       ← proxies:    Account Service (via Kafka)
    ├── GET /api/dashboard        ← aggregates: Portfolio + Market + News (parallel)
    ├── GET /api/v1/market/config ← Market config (symbols, timeframes, candle grids)
    ├── GET /api/v1/market/chart  ← OHLCV candles (Redis cached, Kafka ingest)
    ├── GET /api/news             ← proxies:    News Service (stub)
    └── GET /api/notifications    ← proxies:    Notifications Service (stub)
    │
    ├── Account Service        (Kafka: cmd.account.*)
    ├── Market/Bybit           (HTTP: api.bybit.com, Redis cache, Kafka ingest)
    ├── Portfolio Service      (stub — not yet implemented)
    ├── News Service           (stub — not yet implemented)
    └── Notifications          (stub — not yet implemented)
```

---

## Endpoints

| Method | Path | Auth | Description |
| ------ | ---- | ---- | ----------- |
| GET | `/api/app/bootstrap` | Optional | One-shot app init — user, feature flags, system status |
| GET | `/api/account/me` | Required | Current user profile |
| GET | `/api/dashboard` | Required | Aggregated main screen data |
| GET | `/api/v1/market/config` | None | Symbols, timeframes, candle-count grids, defaults |
| GET | `/api/v1/market/chart` | None | OHLCV candles — `?symbol=BTCUSDT&timeframe=5m&limit=200` |
| GET | `/api/news` | None | Latest news items |
| GET | `/api/notifications` | Required | User notifications |
| GET | `/health` | None | Health check |
| GET | `/swagger` | None (dev only) | Swagger UI |

All responses include `X-Correlation-Id` header.

Полный контракт для frontend-интеграции, включая примеры запросов/ответов и правила обработки degraded/pending состояний, вынесен в [API.md](API.md).

---

## Authentication Flow

1. Client calls `POST /api/account/login` on the Account Service (default host port `7510`) directly — or through the gateway `POST /api/account/login` if you add it.
2. Account Service returns `{ accessToken, refreshToken }`.
3. Client passes `Authorization: Bearer <accessToken>` on all gateway requests.
4. Gateway validates the JWT using the shared `SecretKey` (same as Account Service).
5. Gateway extracts the `sub` / `nameid` claim from the already-validated JWT
   and sends `{ user_id }` over Kafka (`cmd.account.get_user`) instead of
   forwarding the raw bearer downstream.

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
| `JWT_ISSUER` | No | `exchange-app` | JWT issuer claim |
| `JWT_AUDIENCE` | No | `exchange-app-mobile` | JWT audience claim |
| `KAFKA_BOOTSTRAP_SERVERS` | No | `redpanda:29092` | Kafka bootstrap (Redpanda) |
| `ADMIN_SHARED_TOKEN` | Split deployment | — | Shared secret for `/api/admin/*`; admin-host must send the same value as `ADMIN_BACKEND_SHARED_TOKEN` via `Authorization: Bearer`. Mismatch returns `401` with `code=admin_token_invalid`. |

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
| `../deploy/modelline-deploy.yml` | deployment config for image rollout / reconciliation |
| `../deploy/reconcile.ps1` | Windows reconcile script |
| `../deploy/reconcile.sh` | Linux/macOS reconcile script |
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
    Clients/             — Downstream clients (Account via Kafka; rest are stubs)
    Controllers/         — Thin ASP.NET controllers
    DTOs/                — Response contracts and ErrorResponse
    Extensions/          — ServiceCollectionExtensions
    Kafka/               — KafkaSettings, Topics, KafkaRequestClient (request/reply)
    Market/              — Full market API: TimeframeMap, CandleCountGrid, MarketSettings,
                           ChartService, ChartRequestQueue (coalescing), MarketCacheService,
                           DataServiceClient (claim-check detection), BybitClient, MarketIngestService,
                           MarketConfigService, IChartService, ServiceResult<T>
    Middleware/          — CorrelationId, GlobalException
    Settings/            — Strongly typed config sections
../deploy/
  modelline-deploy.yml  — Root-level deployment config
  reconcile.ps1 / .sh   — Root-level reconcile scripts
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
- [ ] `GET /api/app/bootstrap` (no token) → 200, `user` is null, `degradedServices` is empty or has stub services
- [ ] `GET /api/app/bootstrap` (valid JWT) → 200, `user.email` populated
- [ ] `GET /api/account/me` (no token) → 401 JSON with `status`, `title`, `timestamp`
- [ ] `GET /api/dashboard` (no token) → 401
- [ ] `GET /api/news` → 200, `degraded: true` (stub), `items: []`
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
