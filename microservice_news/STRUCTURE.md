# microservice_news — Structure

> **Read before / update after.** Update this file whenever files, modules,
> contracts, the ingest pipeline, the data model, or component ownership change.
> Part of the docs-first contract ([../AGENTS.md](../AGENTS.md)).

---

## Related documentation

- [README.md](README.md) — runbook, HTTP API, ingest pipeline, Kafka contract, constraints
- [../docs/agents/services/microservice_news.md](../docs/agents/services/microservice_news.md) — agent service profile
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — shared docs-first workflow

---

## Service root

| File | Purpose |
| ---- | ------- |
| `NewsService.sln` | .NET solution |
| `Dockerfile` | SDK build → aspnet runtime, non-root `appuser`, `EXPOSE 5000` |
| `docker-compose.yml` | Local stack: `news-api` (`news_service_api`, `7540→5000`) + `news_postgres`; joins `news_net` + external `modelline_net` |
| `global.json` | .NET SDK pin |
| `.env.example` | Env template (port, `DATABASE_URL`, Kafka, CryptoPanic toggles) |
| `README.md` / `STRUCTURE.md` | Service docs |

PostgreSQL data dir is bind-mounted to `../.runtime-data/microservice_news/postgres`.

---

## Clean Architecture layers (`src/`)

Dependency direction: `API → Application → Domain`, `Infrastructure → Application/Domain`.

### `NewsService.Domain/`

Pure domain, no infrastructure deps.

- `Entities/NewsArticle.cs` — the only aggregate. Private setters; factory `Create(...)`
  (trims, upper-cases tags, dedups, UTC-normalizes dates, takes feed `content` when
  present). `ApplyEnrichment(content, imageUrl)` fills **only** still-empty body/image
  (never overwrites the feed) and returns whether anything changed.
  `MarkEnrichmentAttempted()` stamps `EnrichmentAttemptedAt`.

### `NewsService.Application/`

Use-cases and contracts; no EF/Kafka types.

- `Services/NewsAppService.cs` (`INewsAppService`) — `ListAsync` (clamps paging, normalizes
  `symbol`, maps **without** `content`) / `GetAsync` (maps **with** `content`).
- `DTOs/NewsArticleResponse.cs` — `NewsArticleResponse` (incl. nullable detail-only
  `Content`) + `NewsListResponse` (`items/total/page/pageSize`).
- `Interfaces/INewsRepository.cs` — repo contract + `NewsPage`; `INewsEventBus`
  (`PublishCreatedAsync`).
- `Interfaces/IArticleContentEnricher.cs` — `IArticleContentEnricher.EnrichAsync` +
  `ArticleEnrichment(Content, ImageUrl)` record (best-effort, must soft-fail).
- `Common/Settings/CryptoPanicSettings.cs` — ingest settings (`Enabled`,
  `PollIntervalSeconds`, `AuthToken`, `PostsUrl`, hardcoded `RssFeeds[]`) **and**
  `NewsKafkaSettings` (`BootstrapServers`, `NewsEventsTopic`).

### `NewsService.Infrastructure/`

EF Core + external adapters.

- `Data/NewsDbContext.cs` — `DbSet<NewsArticle>`; applies configs from assembly.
- `Data/Configurations/NewsArticleConfiguration.cs` — table/columns, `source_url` unique
  index, `published_at` index, GIN index on `tags`.
- `Repositories/NewsRepository.cs` — `INewsRepository` impl. **Holds the "no picture, no
  publish" feed filter** (`ImageUrl != null && != ""`) and `?symbol=` tag-contains query;
  `ExistsByUrlAsync`, `UpsertAsync` (insert-only, returns true on new row),
  `ListNeedingEnrichmentAsync` (tracked backlog), `UpdateAsync`.
- `Enrichment/SmartReaderContentEnricher.cs` — fetches the page via the shared
  `cryptopanic` client, hands HTML to `SmartReader.Reader`, returns cleaned text +
  featured image; swallows all failures.
- `Migrations/` — `InitialCreate` → `AddNewsArticleContent` (`content`) →
  `AddNewsEnrichmentAttempt` (`enrichment_attempted_at`) + model snapshot.

### `NewsService.API/`

Host, HTTP surface, ingest worker, Kafka producer.

- `Program.cs` — Serilog, DI wiring, controllers, Swagger (Dev), `/health`, then
  `await app.MigrateAndSeedAsync()` before `RunAsync()`. `partial class Program` for tests.
- `Extensions/ServiceCollectionExtensions.cs` — DI: options, DbContext (Npgsql,
  snake_case), repo/app-service/enricher, the **anti-throttle `cryptopanic` HttpClient**
  (browser UA, decompression, forced-IPv4 `ConnectCallback`), `KafkaNewsEventBus`
  singleton, the hosted ingester, Swagger, health checks.
- `Extensions/MigrationExtensions.cs` — `MigrateAndSeedAsync`: logs pending, `MigrateAsync`,
  then `EnsureCoreSchemaAsync` (`RequiredTables = ["news_articles"]`) recreates from model
  if empty / throws on partial.
- `Controllers/NewsController.cs` — `[Route("api/news")] [AllowAnonymous]`: `GET /`
  (list, optional `?symbol=`), `GET /{id:guid}` (detail / 404).
- `BackgroundJobs/CryptoPanicIngesterService.cs` — the ingest pipeline (legacy name,
  RSS-first): RSS/Atom fetch + parse, `content:encoded` full-text, image extraction,
  optional CryptoPanic JSON, dedup, gap-only + backfill enrichment, upsert + event emit.
  Holds all the RSS/HTML parsing helpers (`HtmlToText`, `ExtractImageUrl`, `ParseDate`, …).
- `Kafka/KafkaNewsEventBus.cs` — Confluent producer; emits the `news.created` envelope to
  `events.news.v1`, keyed by article id; warns (never throws) on delivery failure.
- `Middleware/GlobalExceptionMiddleware.cs` — uniform error responses.
- `appsettings.json` / `appsettings.Development.json` — Serilog, empty `ConnectionStrings`
  (env-supplied), Kafka + `News:CryptoPanic` defaults.

---

## Request & ingest flows

- **Read:** client → gateway `api/news` proxy → this `NewsController` → `NewsAppService`
  → `NewsRepository` (image-gated, tag-filtered, paged) → PostgreSQL.
- **Ingest:** timer tick → fetch RSS (+ optional CryptoPanic) → parse → gap-only scrape →
  `UpsertAsync` → on new row, `KafkaNewsEventBus` → `events.news.v1` → consumed by
  `microservice_notification`. A bounded backfill pass heals older incomplete rows.

---

## Ownership notes

- This service **owns the news domain and its PostgreSQL**; no other service writes it.
- It is a **pure producer** on Kafka (`events.news.v1`) — it consumes no topic.
- All ingest/enrichment runs **inside this service**; there is no admin-driven job here.
- No tests; no Kafka request/reply (`cmd.*`) handlers — HTTP read + one event out only.
