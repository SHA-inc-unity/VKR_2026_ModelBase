# API Gateway — Mobile BFF

ASP.NET Core 8 API Gateway (Backend for Frontend) for the Exchange App Flutter client.

The gateway is the **single entry point** for the mobile app. It aggregates responses from multiple downstream services, handles authentication, and returns partial/degraded responses when a service is unavailable.

Downstream IPC is **Kafka-only** (Redpanda broker). The former HTTP client to
`microservice_account` was replaced with `KafkaRequestClient` (async
request/reply on a per-instance `reply.gateway.{instanceId}` inbox).

---

## Architecture

```
Flutter App
    │
    ▼
API Gateway  :5020
    ├── GET /api/app/bootstrap    ← aggregates: Account (optional)
    ├── GET /api/account/me       ← proxies:    Account Service (via Kafka)
    ├── GET /api/dashboard        ← aggregates: Portfolio + Market + News (parallel)
    ├── GET /api/news             ← proxies:    News Service (stub)
    └── GET /api/notifications    ← proxies:    Notifications Service (stub)
    │
    ├── Account Service        (Kafka: cmd.account.*)
    ├── Portfolio Service      (stub — not yet implemented)
    ├── Market Service         (stub — not yet implemented)
    ├── News Service           (stub — not yet implemented)
    └── Notifications          (stub — not yet implemented)
```

---

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/app/bootstrap` | Optional | One-shot app init — user, feature flags, system status |
| GET | `/api/account/me` | Required | Current user profile |
| GET | `/api/dashboard` | Required | Aggregated main screen data |
| GET | `/api/news` | None | Latest news items |
| GET | `/api/notifications` | Required | User notifications |
| GET | `/health` | None | Health check |
| GET | `/swagger` | None (dev only) | Swagger UI |

All responses include `X-Correlation-Id` header.

---

## Authentication Flow

1. Client calls `POST /api/account/login` on the Account Service (port 5010) directly — or through the gateway `POST /api/account/login` if you add it.
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
|----------|----------|---------|-------------|
| `JWT_SECRET_KEY` | Yes | — | Shared HMAC secret (≥32 chars, same as Account Service) |
| `JWT_ISSUER` | No | `exchange-app` | JWT issuer claim |
| `JWT_AUDIENCE` | No | `exchange-app-mobile` | JWT audience claim |
| `KAFKA_BOOTSTRAP_SERVERS` | No | `redpanda:29092` | Kafka bootstrap (Redpanda) |

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
# Gateway:  http://localhost:5020
# Account:  http://localhost:5010
```

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

```
src/
  GatewayService.API/
    Aggregators/         — BFF orchestration logic (Bootstrap, Dashboard)
    Clients/             — Downstream clients (Account via Kafka; rest are stubs)
    Controllers/         — Thin ASP.NET controllers
    DTOs/                — Response contracts and ErrorResponse
    Extensions/          — ServiceCollectionExtensions
    Kafka/               — KafkaSettings, Topics, KafkaRequestClient (request/reply)
    Middleware/          — CorrelationId, GlobalException
    Settings/            — Strongly typed config sections
tests/
  GatewayService.UnitTests/
  GatewayService.IntegrationTests/
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
|---------|---------|---------|
| `Microsoft.AspNetCore.Authentication.JwtBearer` | 8.* | JWT validation |
| `Confluent.Kafka` | 2.* | Kafka producer/consumer for request/reply to downstream services |
| `Serilog.AspNetCore` | 8.* | Structured logging |
| `Swashbuckle.AspNetCore` | 6.* | Swagger/OpenAPI |
