# Account Service

Microservice for authentication and user management. ASP.NET Core 8, Clean Architecture, PostgreSQL, JWT.

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
│   └── AccountService.API/           # Controllers, Middleware, Program.cs
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
