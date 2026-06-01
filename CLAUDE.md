# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

"ModelLine" — a polyglot microservices monorepo (.NET 8 + Next.js 14 + Python 3.12) behind the SHA Trade crypto platform.

## ⚠️ Docs-first workflow is mandatory and enforced

This repo runs a **docs-first** contract for all agentic changes (`AGENTS.md`, `docs/agents/WORKFLOW.md`, and a GitHub Copilot `applyTo:'**'` instruction at `.github/instructions/markdown-governance.instructions.md`). A code task is **not done until the Markdown matches the code.**

- **Before code**, read in order: `README.md` → `STRUCTURE.md` → `docs/agents/README.md` → `docs/agents/WORKFLOW.md` → `docs/agents/DOCS_MAP.md` → `promt_agent.md` → the affected service's `README.md`/`STRUCTURE.md` → its profile in `docs/agents/services/`. (Gateway also requires its `API.md`.)
- **After code**, update: the affected service's `README.md`/`STRUCTURE.md`, its `docs/agents/services/<svc>.md` profile, `promt_agent.md`, and `docs/agents/CHANGE_LOG.md` (append-only). Cross-cutting changes also touch the root `README.md`/`STRUCTURE.md`/`WORKFLOW.md`/`DOCS_MAP.md`.

## Where this runs

The backend runs on a **separate host** — `95.165.27.159` (`sha-300`) at `/mnt/ssd/VKR_2026_ModelBase` (same origin, same commit as this copy). **Editing locally does nothing to the live backend** until you SSH in and rebuild (SSH credentials for user `sha` are in the operator's private `~/.claude/CLAUDE.md`, intentionally not committed here):

```bash
ssh sha@95.165.27.159 \
  'cd /mnt/ssd/VKR_2026_ModelBase && git fetch --all --prune && git reset --hard origin/main && \
   bash ./microservicestarter/restart.sh all noadmin'
```

Live container names differ from the compose service names — for diagnosis: gateway = `exchange-gateway` (image `exchange-app/gateway-service:local`), account = `account_service_api`, social = `social_service_api`, news = `news_service_api`, notification = `notification_service_api`, data = `microservice_data-data-1`, analitic = `microservice_analitic-api-1`, plus per-service `*_postgres`, `redpanda`, `minio`.

## Orchestration — `microservicestarter`

`microservicestarter/services.conf` is the **single source of truth** (one `<name> <path>` line per service; same file on both hosts). Role is chosen by **mode, not config**: the same repo runs as backend (`noadmin`) on the backend host and as admin head (`onlyadmin`) on the admin host.

```bash
cd microservicestarter
./start.sh                      # full local stack (mode=core, NO rebuild)
./restart.sh all noadmin        # backend host: rebuild+restart everything EXCEPT admin (git pull first)
./restart.sh all onlyadmin <BACKEND_HOST>   # admin host: rebuild the online admin head only
./start.sh microservice_data    # single service (positional 2nd arg = mode)
./status.sh                     # docker compose ps per service
./stop.sh microservice_data clean   # DESTRUCTIVE: down --volumes + rm -rf .runtime-data/<svc>
```

- `start.sh` modes: `core`/`noadmin`/`onlyadmin`/`full`/`scheduler`/`build`/`logs`. `restart.sh` modes: `core`/`noadmin`/`onlyadmin`/`full`/`deps`/`api`. **Modes are not shared** — `scheduler`/`build`/`logs` are start-only; `deps`/`api` are restart-only; the wrong combo aborts. (README/STRUCTURE list `api` under `start` — that's wrong, it has no such branch.)
- `start` core does **not** rebuild; `restart` **always** rebuilds. `start onlyadmin` skips `--build`; `restart onlyadmin` builds.
- Multi-service runs bring up `microservice_infra` first (it creates the shared `modelline_net` bridge + Redpanda/MinIO/nginx), then fan out the rest in parallel.
- `.env` is baked in at `docker compose up` — editing any `.env` requires a fresh `start`/`restart`. PowerShell `*.ps1` variants exist for Windows.
- `deploy/reconcile.sh` is a **separate** registry-image deploy path (pulls prebuilt images, `up -d --no-deps`); it does not read `services.conf` and never builds from source. `deploy/run_service_traces.py` runs end-to-end HTTP trace tests.

## Services (9, all registered in `services.conf`)

| Service | Stack | Host port | Role |
| ------- | ----- | --------- | ---- |
| `microservice_infra` | Docker compose: Redpanda, MinIO, nginx | 9092/9644, 9000/9001, 8501 (ingress), 8443 (admin facade), console 8080 | Shared Kafka + S3 + ingress; owns `modelline_net`; start first |
| `microservice_data` | .NET 8 (single `DataService.API`) | 8100 | **Owner of all market data** (own Postgres), dataset-jobs queue, MarketWatcher live prices. No test project. |
| `microservice_account` | .NET 8 Clean Architecture | 7510→5000 | Auth authority: issues/validates **HS256 JWT** (shared secret), users/roles/refresh in Postgres. Tests: Unit/Integration/Contract. |
| `microservice_gateway` | .NET 8 BFF | 7520→5020 | Mobile/web Backend-for-Frontend; `/api/v1/market/*`; validates Account JWT; aggregates with graceful degradation. Tests: Unit/Integration/Contract/Smoke. |
| `microservice_admin` | Next.js 14 / TS, Node 22 | 3000 (via nginx `/admin`, or `:80/:443` in onlyadmin) | Control-plane UI only — **never runs jobs**; Kafka via kafkajs; SQLite state. No test suite. |
| `microservice_analitic` | Python 3.12 / FastAPI / CatBoost | 8000 | ML training/prediction/anomaly; Kafka + Redis; optional scheduler profile. pytest tests (pytest unpinned). |
| `microservice_social` | .NET 8 Clean Architecture | 7530→5000 | Favorites + threaded comments/likes on assets/news; **produces** `events.social.v1`. Own Postgres. No tests; no README. |
| `microservice_news` | .NET 8 Clean Architecture | 7540→5000 | Aggregates crypto news from RSS feeds (+optional CryptoPanic); public read-only feed; **produces** `events.news.v1`. Own Postgres. No tests; no README. |
| `microservice_notification` | .NET 8 Clean Architecture | 7550→5000 | Per-user notification inbox + SSE delivery + price-drift watcher; **consumes** `events.social.v1`/`events.news.v1`. Own Postgres. No tests; no README. |
| `shared` | Python pkg `modelline_shared` | — | Kafka messaging contracts/utilities (Python-only library, not a service) |

> `social`/`news`/`notification` are deployed and running but are **not in the README's service table and have no README/STRUCTURE** — their code is the only source of truth. If you change them, the docs-first rule still applies to root docs.

## Two communication planes (do not mix)

1. **ML/data plane — Kafka/Redpanda only.** `data`, `analitic`, `admin`, `infra` talk *exclusively* over Kafka (`redpanda:29092`); direct service-to-service HTTP between them is forbidden. Topics: `cmd.<svc>.<action>` (request/reply), `reply.<requester>.<instance>` (private inbox), `events.<svc>.<event>`. Large payloads use a **MinIO claim-check** (Kafka carries a reference; bytes flow through MinIO; browser downloads via nginx `:8501/modelline-blobs/*`).
2. **REST + JWT plane.** `gateway` ↔ `account` (and the Flutter client). HS256 JWT issued by `account`, validated everywhere with issuer `account-service`, audience `exchange-app`.

The newer `social`/`news`/`notification` services sit on the REST+JWT plane for the client **and** emit/consume Kafka `events.*.v1`: **`social`+`news` produce → `notification` consumes → pushes to the Flutter client over SSE** (`/api/notifications/stream?access_token=<jwt>`). Service-to-service calls into `social`/`account` `/internal/*` use an `X-Internal-Api-Key` header, not JWT.

> The Kafka topic constants exist in **two hand-synced places**: `shared/modelline_shared/messaging/topics.py` (Python) and `microservice_admin/src/lib/topics.ts` (TS). The .NET services reimplement the envelope/topic contract in C#. A contract change must be propagated to **all** language implementations by hand.

## Per-service build / test conventions

.NET services build/test with `dotnet`; the **.NET SDK is not installed on the admin host** — run on the backend (`95.165.27.159`, dotnet 8.0.127) or in Docker. `account`/`gateway`/`social`/`news`/`notification` set **`TreatWarningsAsErrors=true`** (any warning fails the build). All .NET services auto-apply EF migrations on startup.

```bash
# .NET (account/gateway/social/news/notification): build + test + single test
dotnet build <Service>.sln -c Release
dotnet test                                              # account & gateway only; the other 3 ship NO tests
dotnet test tests/<Project> --filter "FullyQualifiedName~SomeTest"

# analitic (Python): the modelline-base image holds deps; tests need pytest installed manually
./microservice_analitic/microservicestarter/start.sh    # or: docker compose up -d --build
pip install pytest && python -m pytest tests/test_core.py::test_name -v   # run from the service root (conftest sets sys.path)

# admin (Next.js): no test script exists
npm ci && npm run dev          # served under basePath /admin → http://localhost:3000/admin
```

Gotchas: `microservice_data`'s README "Local run" block is **stale** (shows Python; it's C#/.NET — use `dotnet`). `microservice_data` and `microservice_admin` have **no tests**. Every service except `infra` needs the external `modelline_net` network (created by `infra`) to exist first. `analitic`'s Docker build requires the `modelline-base` image (`docker compose --profile build-base build base`) — prefer the `microservicestarter` scripts which order this for you.
