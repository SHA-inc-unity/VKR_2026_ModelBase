# Notification Service

> **Docs-first — read before / update after.** Before changing this service read
> this file → [STRUCTURE.md](STRUCTURE.md) → its profile
> [../docs/agents/services/microservice_notification.md](../docs/agents/services/microservice_notification.md) →
> [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md). After changing it,
> update this README + STRUCTURE.md + the service profile and append a line to
> [../docs/agents/CHANGE_LOG.md](../docs/agents/CHANGE_LOG.md). A code task is not
> done until the Markdown matches the code.

Per-user notification **inbox** + **real-time SSE delivery** + **self-hosted
browser Web Push (VAPID)** + a **price-drift watcher**. ASP.NET Core 8, Clean
Architecture, its own PostgreSQL. It
**consumes** Kafka `events.social.v1` and `events.news.v1` (produced by
`microservice_social` / `microservice_news`) and turns them into per-user
notifications.

Container `notification_service_api`, host port **7550 → 5000**, own Postgres
`notification_postgres`. The Flutter/web client never talks to it directly — it
reaches every endpoint through the **gateway** (`microservice_gateway`
`NotificationsController` forwards `/api/notifications/*` and
`/api/notification-settings` to this service, including the SSE stream).

---

## Agent Documentation

- [STRUCTURE.md](STRUCTURE.md) — file/module map of the service
- [../docs/agents/services/microservice_notification.md](../docs/agents/services/microservice_notification.md) — service profile for the docs-first workflow
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — shared agent workflow for the repository

---

## Stack

| Layer | Technology |
| ----- | ---------- |
| API | ASP.NET Core 8 Web API (Clean Architecture: Domain / Application / Infrastructure / API) |
| Auth | JWT HS256, validated locally (issuer `account-service`, audience `exchange-app`, shared `Jwt:SecretKey`) — Account is the token authority |
| DB | PostgreSQL + EF Core 8 + Npgsql (snake_case naming convention) |
| Realtime | Server-Sent Events (in-process `SseDispatcher`) |
| Kafka | Confluent.Kafka — **consumer only**, `redpanda:29092` |
| Outbound HTTP | `microservice_social` `/internal/*` (via `X-Internal-Api-Key`), gateway `/api/v1/market/snapshot` |
| Docs | Swagger / OpenAPI (Development only) |
| Logging | Serilog → Console |
| Tests | **none** — the code is the only source of truth |

`TreatWarningsAsErrors=true` (any warning fails the build). The **.NET SDK is not
installed on the admin host** — build/run on the backend host (`95.165.27.159`,
dotnet 8.0.127) or via Docker.

---

## Delivery model — inbox + SSE

A notification has three delivery paths:

1. **Inbox (persisted).** Every notification is written to the `notifications`
   table and can be listed / counted / marked-read over HTTP at any time.
2. **SSE stream (ephemeral).** `GET /api/notifications/stream?access_token=<jwt>`
   opens a `text/event-stream`. The `SseDispatcher` is an **in-memory** singleton
   keyed by user id; it only reaches **currently connected** clients. When the
   tab/app is closed there is no SSE connection, so nothing is pushed in
   real-time (the inbox still has the row on next fetch). The query-param token
   path exists because browser `EventSource` cannot set an `Authorization`
   header — the stream endpoint is `[AllowAnonymous]` and validates the token
   manually via `JwtTokenValidator`.
3. **Web Push (VAPID, self-hosted — no Firebase).** Mirrors the SSE path but
   reaches the browser **even when the tab/app is closed**, closing the SSE gap.
   Subscriptions live in **our** Postgres (`push_subscriptions`). Delivery uses
   the `WebPush` NuGet package (`WebPushClient` + `VapidDetails`). It is
   **best-effort**: it never throws out of the fan-out, dead subscriptions
   (HTTP `404`/`410 Gone`) are deleted, other failures bump a per-row
   `failure_count`. Push is **disabled (soft, logged once)** when the VAPID
   private key is empty. The per-user "master toggle" is simply *whether the
   user has a push subscription*; per-kind opt-outs are honored automatically
   because push runs after the opt-out gate below.

**Single fan-out point — `NotificationsAppService.PushAsync`.** Every producer
(Kafka consumer, price watcher, anything new) funnels through it:

```
PushAsync(notification):
  1. per-user opt-out check  (EnableReply / EnableNews / EnablePrice by Kind)
  2. dedup                   (skip if same UserId+Kind+DedupKey already exists)
  3. persist                 (write the inbox row)
  4. SSE push                (deliver to connected clients only)
  5. Web Push                (best-effort VAPID push to the user's subscriptions)
```

Anything new (e.g. FCM/APNs) hooks in **here**, after persist — do not add a
second fan-out path.

---

## Endpoints

All under JWT auth (forwarded by the gateway, which strips/relays the bearer).
User identity comes from the `sub` / `nameid` / NameIdentifier claim.

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET` | `/api/notifications` | List inbox (`?unreadOnly`, `?page`, `?pageSize` ≤ 200), newest first; returns items + total + unread |
| `GET` | `/api/notifications/unread-count` | Unread badge count |
| `POST` | `/api/notifications/{id}/read` | Mark one read (user-scoped) |
| `POST` | `/api/notifications/read-all` | Mark all read |
| `GET` | `/api/notifications/stream?access_token=<jwt>` | SSE stream (`event: notification`), 25 s keep-alive comments |
| `GET` | `/api/notifications/push/public-key` | **`[AllowAnonymous]`** — VAPID public key for the browser `PushManager` |
| `POST` | `/api/notifications/push/subscribe` | Upsert a Web Push subscription for the current user (`{ endpoint, keys:{ p256dh, auth }, userAgent? }`) |
| `POST` | `/api/notifications/push/unsubscribe` | Remove a Web Push subscription by `{ endpoint }` |
| `GET` | `/api/notification-settings` | Read per-user toggles (auto-creates defaults) |
| `PUT` | `/api/notification-settings` | Update toggles / threshold |
| `GET` | `/health` | Liveness + EF `DbContext` check |

---

## Notification kinds

| Kind | Source | Trigger | Per-user toggle |
| ---- | ------ | ------- | --------------- |
| `comment.reply` | Kafka `events.social.v1` `comment.created` | Someone replied to your comment (recipient resolved from social `/internal/comments/{parentId}/author`; never notifies the author of their own reply) | `EnableReply` |
| `news.favorite` | Kafka `events.news.v1` `news.created` | News tagged with a symbol you favorited (recipients from social `/internal/favorites/users-by-symbol/{tag}`) | `EnableNews` |
| `price.favorite` | `PriceDriftWatcherService` | A symbol you favorited moved beyond your `%` threshold | `EnablePrice` |

Per-user settings (`notification_settings`, one row/user, defaults `true` /
`true` / `true` / `5%`): `EnableReply`, `EnableNews`, `EnablePrice`,
`PriceThresholdPct` (clamped to `0.0001%..100%`).

### Price drift

`PriceDriftWatcherService` polls on an interval (default `PriceWatcher:PollIntervalSeconds`
= 300 s, floored at 60 s; 30 s warm-up; disable with `PriceWatcher:Enabled=false`).
Each tick it builds the tracked-symbol set = a small well-known baseline (BTC/ETH/…)
**∪ the symbols users actually favorited** (social `/internal/favorites/symbols`),
fetches a price snapshot from the gateway (`/api/v1/market/snapshot`), and
compares against the **last in-memory snapshot** it holds. If a symbol moved by
≥ the user's `PriceThresholdPct`, it pushes `price.favorite` (24 h dedup bucket
per symbol+direction). It is **O(symbols)**, not O(users). There is a **single
`%` threshold per user** — no per-symbol or target-price rules.

> **Known gap.** Price alerts come **only** from this watcher over favorited
> symbols. The gateway's `/api/alerts` rows (`AlertsController` →
> `IFrontendContractState`) are **stored but never evaluated** by this service —
> they do not produce notifications today.

---

## Configuration

Wired through `docker-compose.yml` env (`Section__Key` → `appsettings`):

| Env | Section | Default | Meaning |
| --- | ------- | ------- | ------- |
| `ConnectionStrings__DefaultConnection` | — | — | Postgres connection string |
| `Jwt__SecretKey` / `Jwt__Issuer` / `Jwt__Audience` | `Jwt` | — / `account-service` / `exchange-app` | Shared HS256 validation params |
| `Kafka__BootstrapServers` | `Kafka` | `redpanda:29092` | Broker |
| `Kafka__SocialEventsTopic` / `Kafka__NewsEventsTopic` | `Kafka` | `events.social.v1` / `events.news.v1` | Consumed topics |
| `Kafka__GroupId` | `Kafka` | `notification-service` | Consumer group |
| `SocialService__BaseUrl` / `SocialService__InternalApiKey` | `SocialService` | `http://social_service_api:5000` / — | Social `/internal/*` directory lookups (`X-Internal-Api-Key`) |
| `Gateway__BaseUrl` | `Gateway` | `http://exchange-gateway:5000` | Market snapshot source |
| `PriceWatcher__PollIntervalSeconds` / `PriceWatcher__Enabled` | `PriceWatcher` | `300` / `true` | Drift-watcher cadence / kill-switch |
| `Push__VapidPublicKey` | `Push` | committed default | Public VAPID key served to browsers (safe to commit) |
| `Push__VapidPrivateKey` | `Push` | **empty** | Secret VAPID private key. **Never committed** — injected via host-only `.env` (`PUSH_VAPID_PRIVATE_KEY`). Empty ⇒ push disabled (soft, logged once) |
| `Push__VapidSubject` | `Push` | `mailto:admin@sha-trade.tech` | VAPID `sub` contact (mailto/URL) |

---

## Rules & constraints

- **Build/run on backend or Docker** — no .NET SDK on the admin host; `TreatWarningsAsErrors=true`.
- **EF auto-migrate on boot** (`MigrateAndSeedAsync`): applies pending migrations, then a `RequiredTables` whitelist (`notifications`, `notification_settings`, `push_subscriptions`) sanity-check — if the schema is fully empty after migrate it recreates tables from the model; a *partial* schema is a hard-fail.
- **Web Push secret is never committed.** `appsettings.json` ships an empty `Push:VapidPrivateKey`; the secret is provided only via the host `.env` (`PUSH_VAPID_PRIVATE_KEY`) → `Push__VapidPrivateKey`. With no private key, push is disabled (logged once), inbox/SSE keep working.
- **Kafka is consume-only** here (`redpanda:29092`). The envelope contract (`type` + `payload`) is reimplemented in C# and hand-synced with the rest of the repo — see root `CLAUDE.md` on the two communication planes and the hand-synced topic constants.
- **SSE is in-process** — a single replica; horizontal scale-out would need a shared bus, because `SseDispatcher` only knows about clients connected to *this* instance.
- **Docs-first** — update this README + STRUCTURE.md + the service profile + CHANGE_LOG after any change to endpoints, kinds, Kafka contracts, the delivery model, or config.
