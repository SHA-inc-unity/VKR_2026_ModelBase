# microservice_social — Social Service

> **Read before / update after.** This README and [STRUCTURE.md](STRUCTURE.md) are the docs-first
> source of truth for this service: read them before touching the code, and update them (plus
> [../docs/agents/services/microservice_social.md](../docs/agents/services/microservice_social.md) and
> [../docs/agents/CHANGE_LOG.md](../docs/agents/CHANGE_LOG.md)) whenever behaviour, contracts, the data
> model or constraints change. A code task is **not done** until these docs match the code.

---

## What it is

ASP.NET Core 8 microservice, **Clean Architecture** (Domain → Application → Infrastructure → API), owner
of the social layer of the SHA Trade platform. It owns the entire `/api/social/*` surface:

- **Favorites** — a user's watchlisted symbols.
- **Threaded comments + likes** — comments on assets and news, one level of replies, per-comment likes.
- **Per-coin sentiment** — bullish/bearish voting per target, one persistent vote per user.

Each concern has its own **PostgreSQL** database (EF Core 8, `snake_case` naming convention). The service
sits on the **REST + JWT plane**: it validates the same shared **HS256 JWT** that `microservice_account`
issues (issuer `account-service`, audience `exchange-app`), and it **produces** Kafka events on
`events.social.v1` (consumed by `microservice_notification`).

| Fact | Value |
| ---- | ----- |
| Stack | .NET 8, ASP.NET Core, EF Core 8 (Npgsql + `EFCore.NamingConventions` snake_case) |
| Architecture | Clean Architecture: `SocialService.Domain` / `.Application` / `.Infrastructure` / `.API` |
| Database | own PostgreSQL `social_service` (container `social_postgres`) |
| Container | `social_service_api`, host port **7530 → 5000** (`ASPNETCORE_URLS=http://+:5000`) |
| Auth | JWT Bearer, HS256, issuer `account-service`, audience `exchange-app`, shared `Jwt:SecretKey` |
| Kafka | **produces** `events.social.v1` (best-effort; broker outage never fails the request) |
| Internal API | `/internal/*` guarded by `X-Internal-Api-Key`, **not** JWT |
| Health | `GET /health` (DbContext check) |

The Flutter/web client does **not** call this service directly. The gateway's `SocialController`
forwards `/api/social/*` to it (BFF pass-through). Author display names are resolved from
`microservice_account` over its `/internal/users/{id}` route (HTTP + `X-Internal-Api-Key`,
`HttpUserDirectoryService`).

---

## Endpoints

All client-facing routes live under `/api/social/*` and are reached **through the gateway**, not directly.

### Comments — `CommentsController` (`/api/social/comments`)

| Method | Path | Auth | Description |
| ------ | ---- | ---- | ----------- |
| GET | `/api/social/comments` | Anonymous | List comments for a target. Query: `targetType` (`asset`\|`news`), `targetId`, `page`, `pageSize` (≤200), `sort` (`new`\|`top`\|`top24h`/`hot`/`trending`, default `top24h`). A valid JWT, if present, populates `likedByMe`. |
| POST | `/api/social/comments` | **Required** | Create a comment/reply. Body: `targetType`, `targetId`, `body` (1..4000 chars), optional `parentId`. Replies are flattened to one level (a reply to a reply re-parents to the thread root). |
| PATCH | `/api/social/comments/{id}` | **Required** | Edit a comment body. Author **or** admin only. |
| DELETE | `/api/social/comments/{id}` | **Required** | Soft-delete (`deleted_at` set, body preserved for moderation, hidden in payloads). Author **or** admin only. |
| POST | `/api/social/comments/{id}/like` | **Required** | Like (idempotent — duplicate likes are deduped by the composite PK). |
| DELETE | `/api/social/comments/{id}/like` | **Required** | Unlike. |

### Favorites — `FavoritesController` (`/api/social/favorites`, whole controller `[Authorize]`)

| Method | Path | Auth | Description |
| ------ | ---- | ---- | ----------- |
| GET | `/api/social/favorites` | **Required** | List the caller's favorite symbols. |
| PUT | `/api/social/favorites/{symbol}` | **Required** | Add a favorite (symbol upper-cased; idempotent). |
| DELETE | `/api/social/favorites/{symbol}` | **Required** | Remove a favorite. |

### Sentiment — `SentimentController` (`/api/social/sentiment`)

| Method | Path | Auth | Description |
| ------ | ---- | ---- | ----------- |
| GET | `/api/social/sentiment` | Anonymous | Aggregate for a target. Query: `targetType`, `targetId`. Returns `{ bullish, bearish, total, myVote }`; `myVote` is `none` for anonymous viewers or those without a vote. |
| POST | `/api/social/sentiment` | **Required** | Cast/move/retract the caller's vote. Body: `targetType`, `targetId`, `vote` (`bullish`\|`bearish`\|`none`). `none` deletes the row; `bullish`/`bearish` upsert. Returns the fresh aggregate projected for the voter. |

### Internal — `InternalController` (`/internal/*`, `X-Internal-Api-Key`, **not** JWT)

Server-to-server only (used by `microservice_notification`). Returns `401` if the key is missing/wrong.

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/internal/comments/{id}/author` | Author id of a comment (`{ authorId }`) — lets notifications know who to notify. |
| GET | `/internal/favorites/users-by-symbol/{symbol}` | User ids who favorited a symbol (base↔quote candidate matching). |
| GET | `/internal/favorites/symbols` | All distinct favorited symbols across users — the notification price-drift watcher tracks exactly these. |

### Infra

| Method | Path | Auth | Description |
| ------ | ---- | ---- | ----------- |
| GET | `/health` | None | Liveness + DbContext connectivity. |
| GET | `/swagger` | None (Development only) | Swagger UI. |

---

## Data model

EF Core 8, `snake_case`. Configurations live in
`src/SocialService.Infrastructure/Data/Configurations/`.

| Table | Key | Notes |
| ----- | --- | ----- |
| `favorites` | PK `{user_id, symbol}` | One row per user/symbol; symbols stored upper-cased. Index on `symbol`. |
| `comments` | PK `id` (client-generated GUID) | Threaded via nullable `parent_id` (one reply level). `target_type` (`asset`\|`news`), `target_id`, `body` (≤4000), `created_at`/`updated_at`, nullable `deleted_at` (soft delete). Index `{target_type, target_id, created_at}` and `parent_id`. |
| `comment_likes` | PK `{comment_id, user_id}` | The composite PK **is** the like-dedup guarantee. Index on `comment_id`. |
| `asset_sentiment` | PK `{user_id, target_type, target_id}` | The composite PK guarantees **one vote per user per target**. `vote` is `bullish`\|`bearish` (`none` never persists — it deletes the row). Counts are produced by a `GROUP BY vote` aggregate over `{target_type, target_id}` (indexed). Votes are **persistent until changed/retracted** — there is no daily reset. |

**Migrations**: `src/SocialService.Infrastructure/Migrations/`
(`InitialCreate`, `AddAssetSentiment`). They **auto-apply on boot** (`MigrateAndSeedAsync` in
`Program.cs` → `MigrationExtensions`). After migration, the startup check verifies a `RequiredTables`
whitelist (`favorites`, `comments`, `comment_likes`, `asset_sentiment`); if **all** are missing it
recreates the schema from the model, and a **partial** schema throws. **Any new table must be added to
`RequiredTables`** or boot fails.

---

## Rules / constraints

- **`TreatWarningsAsErrors=true`** in every project — the build fails on any warning. Keep it warning-clean.
- **No .NET SDK on the admin host.** Build/test on the backend host (`95.165.27.159`, dotnet 8.0.127) or in Docker.
- **EF migrations**: add via `dotnet ef migrations add <Name>` (or hand-author the migration + its
  `.Designer.cs` + the `SocialDbContextModelSnapshot` block consistently) **and** add the new table to
  `RequiredTables` in `src/SocialService.API/Extensions/MigrationExtensions.cs`.
- **Internal calls** use `X-Internal-Api-Key`, never JWT. Keep `/internal/*` off the client surface.
- **Kafka is best-effort**: `KafkaEventBus` logs and swallows publish failures so a broker outage never
  breaks a user request. Notifications are eventual, not guaranteed.
- **Docs-first**: update this README, [STRUCTURE.md](STRUCTURE.md), the service profile and the
  `CHANGE_LOG.md` whenever you change behaviour, contracts, the data model or constraints.

---

## Run / test

```bash
# Build (on the backend host or in Docker — not on the admin host)
dotnet build SocialService.sln -c Release

# Local container
cp .env.example .env        # set DATABASE_URL, JWT_SECRET_KEY, INTERNAL_API_KEY, ...
docker compose up --build   # social_service_api on :7530, social_postgres alongside
```

**There is no test project** — the service ships no unit/integration tests; the code is the source of truth.

**Deploy** (backend host) is via the orchestrator:

```bash
ssh sha@95.165.27.159 \
  'cd /mnt/ssd/VKR_2026_ModelBase && git fetch --all --prune && git reset --hard origin/main && \
   bash ./microservicestarter/restart.sh all noadmin'
```

Editing locally has **no effect** on the live backend until the containers are rebuilt on the backend host.

---

## Environment variables

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `SOCIAL_BIND_ADDR` | `0.0.0.0` | Host bind address for the published port |
| `SOCIAL_API_PORT` | `7530` | Host port mapped to container `5000` |
| `DATABASE_URL` (`ConnectionStrings__DefaultConnection`) | — | Npgsql connection string for `social_service` |
| `JWT_SECRET_KEY` (`Jwt__SecretKey`) | — | Shared HMAC secret (same as Account/Gateway) |
| `JWT_ISSUER` (`Jwt__Issuer`) | `account-service` | JWT issuer claim |
| `JWT_AUDIENCE` (`Jwt__Audience`) | `exchange-app` | JWT audience claim |
| `KAFKA_BOOTSTRAP_SERVERS` (`Kafka__BootstrapServers`) | `redpanda:29092` | Kafka/Redpanda bootstrap |
| `KAFKA_SOCIAL_TOPIC` (`Kafka__SocialEventsTopic`) | `events.social.v1` | Produced events topic |
| `ACCOUNT_BASE_URL` (`AccountService__BaseUrl`) | `http://account_service_api:5000` | Account base URL for `/internal/users/{id}` lookups |
| `INTERNAL_API_KEY` (`AccountService__InternalApiKey` + `InternalApi__ApiKey`) | — | Shared internal key: outbound to Account and inbound guard for this service's `/internal/*` |

---

## Agent documentation

- [STRUCTURE.md](STRUCTURE.md) — directory/layer map and component responsibilities
- [../docs/agents/services/microservice_social.md](../docs/agents/services/microservice_social.md) — service profile for docs-first work
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — shared repository workflow for agents
