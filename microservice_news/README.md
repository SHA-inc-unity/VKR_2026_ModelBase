# News Service

> **Read before / update after.** This repo is docs-first ([../AGENTS.md](../AGENTS.md)).
> Read this file + [STRUCTURE.md](STRUCTURE.md) + the service profile
> [../docs/agents/services/microservice_news.md](../docs/agents/services/microservice_news.md)
> **before** touching the code, and update them (plus
> [../docs/agents/CHANGE_LOG.md](../docs/agents/CHANGE_LOG.md)) **after** any change
> to behavior, contracts, structure, the ingest pipeline, or constraints.

Microservice that aggregates crypto news from public **RSS feeds** (plus an optional
CryptoPanic JSON source) into its own PostgreSQL, serves a **public read-only feed**
to the client through the gateway, and **produces** the Kafka topic
`events.news.v1` consumed by `microservice_notification`. ASP.NET Core 8, Clean
Architecture, EF Core 8 + Npgsql.

There is **no ingest API and no auth** — the feed endpoints are anonymous reads;
ingestion happens entirely inside a background hosted service. This service does
**not** consume any Kafka topic; it only emits one.

---

## Agent Documentation

- [STRUCTURE.md](STRUCTURE.md) — file/module map of the service
- [../docs/agents/services/microservice_news.md](../docs/agents/services/microservice_news.md) — service profile for the docs-first workflow
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — shared agent workflow for the repository

---

## Stack & deployment

| Layer | Technology |
| ----- | ---------- |
| API | ASP.NET Core 8 Web API (controllers) |
| DB | PostgreSQL 16 + EF Core 8 + Npgsql (snake_case naming convention) |
| Ingest | `BackgroundService` polling RSS + optional CryptoPanic JSON |
| Full-text enrichment | SmartReader (Mozilla Readability port) — best-effort scrape |
| Messaging | Confluent.Kafka producer → `events.news.v1` |
| Logging | Serilog (console) |
| Docs | Swagger / OpenAPI (Development only) |

- **Container:** `news_service_api` (image built from the local `Dockerfile`).
- **Port:** host `7540` → container `5000` (`ASPNETCORE_URLS=http://+:5000`).
- **Own PostgreSQL:** container `news_postgres`, DB `news_service`, data dir bind-mounted to
  `../.runtime-data/microservice_news/postgres` (wiped by `microservicestarter` `stop … clean`).
- **Networks:** joins both `news_net` (its private bridge) and the shared external
  `modelline_net` (Redpanda + the gateway live there). `microservice_infra` must be up first.
- **Build:** `dotnet build NewsService.sln -c Release`. All four projects set
  **`TreatWarningsAsErrors=true`** — any warning fails the build. The **.NET SDK is not
  installed on the admin host**; build/run on the backend host (`95.165.27.159`) or in Docker.
  **No test project ships** with this service.

---

## HTTP API

Public, anonymous, JSON. The Flutter client never calls this service directly — it
reaches it through the gateway, which proxies `api/news` 1:1 (the gateway also adds a
`GET /api/news/home?limit=` teaser route and stamps a short `Cache-Control: max-age=30`).

| Method | Route | Description |
| ------ | ----- | ----------- |
| `GET` | `/api/news?page=&pageSize=&symbol=` | Paginated feed, newest first. `symbol` (optional) filters by tag (Postgres `text[]` contains, upper-cased). List rows **omit** the heavy `content` body. |
| `GET` | `/api/news/{id:guid}` | Single article incl. full `content`; `404` if unknown. |
| `GET` | `/health` | Liveness + EF `DbContext` DB check. |

- Pagination is clamped in `NewsAppService`: `page ≥ 1`, `1 ≤ pageSize ≤ 100` (default `30`).
  The feed is deep — paging walks the whole stored history (infinite-scroll friendly).
- The list is **intentionally fresh**: the news service does no caching of its own (the
  client doesn't cache it either); only the gateway applies a short 30 s edge cache.
- `NewsListResponse` = `{ items, total, page, pageSize }`; each item is
  `{ id, source, sourceUrl, title, summary, content (detail-only), imageUrl, publishedAt, tags }`.

---

## Ingest pipeline (background)

`CryptoPanicIngesterService` (the class name is legacy; it is now an RSS-first
aggregator) runs every `PollIntervalSeconds` (min 60 s, default 300 s) after a 15 s
warm-up, when `Enabled`:

1. **Fetch RSS** for each configured feed through a shared `cryptopanic` `HttpClient`
   tuned to beat CDN throttling (real browser User-Agent, `Accept-Language`,
   gzip/deflate/br auto-decompression, **forced IPv4** to dodge broken AAAA routes,
   redirects). RSS 2.0 `<item>` with an Atom `<entry>` fallback.
2. **Parse** each entry → title, link, summary, published date, tags, and a hero image.
   **Full text comes from the feed's own `content:encoded` / `atom:content` when present**
   — the primary, most reliable body source. Image is taken from `media:content` →
   `media:thumbnail` → `enclosure` → first inline `<img>` in the body, in that order.
3. **Optional CryptoPanic JSON** is queried additionally on each tick **only if an
   `AuthToken` is configured** (`?public=true&filter=hot`); those items carry no image.
4. **Dedup** the batch by `sourceUrl`; skip any URL already stored (`ExistsByUrlAsync`) —
   first ingest is canonical, so a story is never re-scraped once it's in the DB.
5. **Enrichment (SmartReader)** runs **only when the feed left a gap** (missing `content`
   *or* missing `imageUrl`): a time-boxed (12 s) best-effort scrape of the source page
   extracts the readable body + a featured/`og:` image. Full-text feeds skip this
   entirely. Any failure (paywall, unreadable, network) is swallowed — the article still
   lands with whatever the feed gave.
6. **Store + emit:** insert via `UpsertAsync`; on a genuinely new row, produce
   `news.created` to `events.news.v1`.
7. **Backfill pass:** up to 8 older, never-attempted, still-incomplete articles get **one**
   enrichment try each per tick (stamped `enrichment_attempted_at` regardless of outcome,
   so permanently-unreadable pages aren't retried forever). This gradually heals history —
   notably it backfills missing hero images, which is what makes a previously-hidden
   article eligible to appear (see the image policy below).

### Image policy — "no picture, no publish" (verified)

`NewsRepository.ListAsync` filters out any article with a null/empty `image_url`, so an
**imageless article is never surfaced in the feed**. It still lives in the DB and becomes
visible once the enrichment backfill discovers an image for it. This is a query-time
display rule, not an ingest-time drop: rows are always stored.

---

## Data model

Single table `news_articles` (entity `NewsArticle`, snake_case columns):

| Column | Notes |
| ------ | ----- |
| `id` | GUID PK |
| `source`, `source_url` | source label + canonical URL (`source_url` is **unique**) |
| `title`, `summary` | headline + short text (summary capped ~2000 chars) |
| `content` | full readable body, plain text with blank-line paragraphs; **nullable**, detail-only, capped ~24k chars |
| `image_url` | hero image; nullable but **required for the article to appear in the feed** |
| `published_at` | source publish time (UTC); feed order key |
| `tags` | `text[]`, upper-cased, GIN-indexed (powers `?symbol=`) |
| `ingested_at` | first-seen timestamp |
| `enrichment_attempted_at` | nullable; one-shot backfill bookkeeping |

Indexes: unique `ux_news_articles_source_url`, `ix_news_articles_published_at`,
GIN `ix_news_articles_tags_gin`.

**Migrations auto-apply on boot** (`MigrateAndSeedAsync` in `Program.cs`): runs
`MigrateAsync()`, then a `RequiredTables` guard (`news_articles`) recreates the schema
from the EF model if migration left the DB empty, and throws on a partial schema.
Migrations: `InitialCreate` → `AddNewsArticleContent` (`content`) →
`AddNewsEnrichmentAttempt` (`enrichment_attempted_at`).

---

## Kafka contract

| Topic | Direction | Type | Payload |
| ----- | --------- | ---- | ------- |
| `events.news.v1` | **out** (produce) | event | `news.created` envelope: `{ type, occurredAt, payload: { newsId, title, tags, source, sourceUrl, publishedAt } }`, camelCase, keyed by article id |

Consumed downstream by `microservice_notification`. This service consumes **nothing**.
A change to the envelope/topic must stay consistent with the cross-language topic
constants (`shared/modelline_shared/messaging/topics.py`,
`microservice_admin/src/lib/topics.ts`).

---

## Configuration (env)

Baked in at `docker compose up` — editing `.env` needs a fresh `start`/`restart`.

| Var | Default | Meaning |
| --- | ------- | ------- |
| `NEWS_API_PORT` / `NEWS_BIND_ADDR` | `7540` / `0.0.0.0` | host port / bind |
| `DATABASE_URL` → `ConnectionStrings__DefaultConnection` | — | Npgsql connection string |
| `KAFKA_BOOTSTRAP_SERVERS` | `redpanda:29092` | broker |
| `KAFKA_NEWS_TOPIC` | `events.news.v1` | produced topic |
| `CRYPTOPANIC_ENABLED` | `true` | master switch for the ingester |
| `CRYPTOPANIC_POLL_SECONDS` | `300` | poll interval (min 60) |
| `CRYPTOPANIC_AUTH_TOKEN` | empty | optional — enables the CryptoPanic JSON source |

The RSS feed list is **not** an env var — it's a hardcoded default in
`CryptoPanicSettings.RssFeeds` (DailyHodl, CoinJournal — full-text; Cointelegraph,
CoinDesk, Decrypt, Bitcoin Magazine — headline + image, body via scrape).

---

## Rules / constraints

- **Docs-first:** update this README, `STRUCTURE.md`, the service profile, `promt_agent.md`
  and `CHANGE_LOG.md` after any code change (see top note).
- `TreatWarningsAsErrors=true` everywhere — warnings break the build.
- No .NET SDK on the admin host — build on the backend host or via Docker; no tests ship.
- EF auto-migrate + `RequiredTables` self-heal on boot.
- Image policy "no picture, no publish" is a **feed display rule**, not an ingest drop.
- The feed is intentionally fresh (no service-side cache; client doesn't cache it).
- Produces `events.news.v1` for `notification`; consumes nothing.
