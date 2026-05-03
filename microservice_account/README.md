# Account Service

Microservice for authentication and user management. ASP.NET Core 8, Clean Architecture, PostgreSQL, JWT.

Inter-service IPC is **Kafka-only** (Redpanda broker). The service exposes HTTP
only for end-user traffic (login/register/refresh) and the `/health` liveness
endpoint — other ModelLine services talk to it via `cmd.account.*` topics.

---

## Agent Documentation

- [STRUCTURE.md](STRUCTURE.md) — file/module map of the service
- [../docs/agents/services/microservice_account.md](../docs/agents/services/microservice_account.md) — service profile for the docs-first workflow
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — shared agent workflow for the repository

---

## Stack

| Layer | Technology |
|-------|-----------|
| API | ASP.NET Core 8 Web API |
| Auth | JWT HS256 (access 15 min + refresh 30 days) |
| DB | PostgreSQL + EF Core 8 + Npgsql |
| Passwords | BCrypt.Net-Next (work factor 12) |
| Validation | FluentValidation 11 |
| Docs | Swagger / OpenAPI |
| Logging | Serilog → Console |
| IPC | Kafka (Confluent.Kafka 2.*) — request/reply via `cmd.account.*` topics |
| Cache (opt) | Redis — access token blacklist |
| Tests | xUnit + Moq + FluentAssertions + Testcontainers |

---

## Project Structure

```
microservice_account/
├── src/
│   ├── AccountService.Domain/        # Entities, Enums — no dependencies
│   ├── AccountService.Application/   # DTOs, Interfaces, Services, Validators
│   ├── AccountService.Infrastructure/# EF Core, Repositories, Cache
│   └── AccountService.API/           # Controllers, Middleware, Program.cs, Kafka/
├── tests/
│   ├── AccountService.UnitTests/
│   ├── AccountService.IntegrationTests/  # Testcontainers PostgreSQL
│   └── AccountService.ContractTests/
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## REST Endpoints

### Public

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/account/register` | Register new user |
| POST | `/api/account/login` | Login, receive tokens |
| POST | `/api/account/refresh` | Refresh access token |
| POST | `/api/account/logout` | Revoke refresh token |

### Protected (Bearer JWT)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/account/me` | Current user profile |
| PUT | `/api/account/profile` | Update username |
| GET | `/api/account/settings` | Get user settings |
| PUT | `/api/account/settings` | Update settings |

### Internal (X-Internal-Api-Key header)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/internal/users/{id}` | Get user by ID |
| GET | `/internal/users/by-email/{email}` | Get user by email |
| GET | `/internal/users/{id}/roles` | Get user roles |

### Kafka (inter-service IPC)

Topics are consumed by `KafkaConsumerService` (GroupId `microservice_account`).
Incoming envelope: `{ correlation_id, reply_to, payload }`. The reply is
published to `reply_to` as `{ correlation_id, payload }`.

| Topic | Payload | Reply |
|-------|---------|-------|
| `cmd.account.health`   | `{}` | `{ status: "ok", service: "microservice_account", version: "1.0.0" }` |
| `cmd.account.get_user` | `{ user_id: Guid }` | `{ id, email, username, status, roles[], created_at }` or `{ error: "not_found" }` |

Bootstrap: `Kafka__BootstrapServers` (env), default `redpanda:29092`. Registered
in `ServiceCollectionExtensions.AddAccountServices`: `KafkaSettings` (config
section `Kafka`), `KafkaProducer` (singleton), `KafkaConsumerService`
(`AddHostedService`). Compose attaches the service to both `account_net` and
the external `modelline_net` so it can reach Redpanda.

---

## Local Setup (Runbook)

### 1. Prerequisites
- .NET 8 SDK
- PostgreSQL 15+ (or Docker)
- (Optional) Redis

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, JWT_SECRET_KEY, INTERNAL_API_KEY
```

### 3. Run with Docker Compose

```bash
# Core services (API + PostgreSQL)
docker compose up -d

# With Redis
docker compose --profile with-redis up -d
```

### 4. Run locally (without Docker)

```bash
# Set connection string in appsettings.Development.json
cd src/AccountService.API
dotnet run
```

Swagger: http://localhost:5000/swagger

### 5. Migrations

Migrations run automatically on startup via `MigrateAndSeedAsync()`.

To add a new migration:
```bash
cd src/AccountService.Infrastructure
dotnet ef migrations add MigrationName \
  --startup-project ../AccountService.API \
  --context AccountDbContext
```

### 6. Run Tests

```bash
# Unit tests (no infra needed)
dotnet test tests/AccountService.UnitTests

# Integration tests (requires Docker for Testcontainers)
dotnet test tests/AccountService.IntegrationTests

# All tests
dotnet test
```

---

## Security Notes

- Passwords: BCrypt (never plaintext, never MD5/SHA1)
- Refresh tokens: stored as SHA-256 hash only
- JWT: HS256, short TTL (15 min), JTI for blacklisting
- Internal API: API key header only, not JWT
- DB user should have minimal privileges (SELECT/INSERT/UPDATE/DELETE only)
- Rotate `JWT_SECRET_KEY` via refresh token rotation (all sessions will invalidate)

---

## EF Core performance notes

The repositories use `AsNoTracking()` everywhere except where the caller
must mutate the returned entity:

| Method | Tracking | Why |
|--------|----------|-----|
| `UserRepository.GetByIdAsync` | tracked | mutated by `UpdateProfileAsync` |
| `UserRepository.GetSettingsAsync` | tracked | mutated by `UpdateSettingsAsync` |
| `RefreshTokenRepository.GetByHashAsync` | tracked | revoked by `RefreshAsync`/`LogoutAsync` |
| `UserRepository.GetByEmailAsync` | no-track | login flow only reads |
| `UserRepository.GetByIdWithRolesAsync` | no-track + `AsSplitQuery()` | profile/internal/refresh — read-only |
| `UserRepository.{Email,Username}ExistsAsync` | no-track | existence check only |
| `RoleRepository.{GetByCode,GetUserRoleCodes}Async` | no-track | read-only catalogue |

**Set-based revoke.** `RevokeAllUserTokensAsync` uses
`ExecuteUpdateAsync(SetProperty(t => t.RevokedAt, now))` — a single SQL
UPDATE, no entities materialised. The previous "load all + loop +
SaveChanges" path round-tripped every active token across the wire and
held them in EF's change-tracker until commit.

**No duplicate role queries.** `GetByIdWithRolesAsync` already eagerly
loads `UserRoles → Role`. `AccountAppService` projects role codes from
the in-memory graph via the private `ExtractRoleCodes(user)` helper —
saving one extra DB query per `Refresh` / `GetCurrentUser` /
`GetInternalUser` call.

**Indexes.** All hot filter columns are indexed (initial migration
`InitialCreate`):
`users(email)`, `users(username)`, `refresh_tokens(token_hash)`,
`refresh_tokens(user_id)`, `audit_login_events(user_id)`,
`roles(code)`, `user_roles(role_id)`.
