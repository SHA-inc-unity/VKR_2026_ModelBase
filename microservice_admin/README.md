# microservice_admin

**Роль:** Admin UI платформы ModelLine. Next.js 14 (App Router). Коммуницирует с `microservice_data` и `microservice_analitic` **исключительно через Kafka** (Redpanda). Никакого прямого HTTP между application-сервисами. Фоновые jobs здесь не исполняются: admin только отправляет команды, читает статусы и показывает jobs, которые фактически выполняются во владельцах-доменных сервисах.

**Стек:** Next.js 14, React 18, TypeScript 5, Tailwind CSS 3, shadcn/ui, Radix UI, kafkajs, node:sqlite, recharts  
**Конфиги:** `tailwind.config.js` + `postcss.config.js` (последний критичен — регистрирует `tailwindcss` и `autoprefixer` как PostCSS плагины, без него `@tailwind` директивы не обрабатываются)  
**Base path:** `/admin` — приложение обслуживается по пути `/admin` (настроено через `basePath` и `assetPrefix` в `next.config.js`). Next.js **не** применяет `basePath` автоматически к `fetch()` и `EventSource` — все клиентские обращения к API используют `process.env.NEXT_PUBLIC_BASE_PATH ?? ''` как префикс (`healthClient.ts`, `kafkaClient.ts`, `useEvents.ts`).

## Docker image base

`microservice_admin/Dockerfile` использует официальный образ Docker Hub
`node:22-bookworm-slim` и для build stage, и для runtime stage. Это убирает
зависимость сборки от `cgr.dev`, что критично для Linux-серверов, где
registry `cgr.dev` может быть недоступен, а `docker compose up --build`
должен работать штатно. В build stage также всегда создаётся каталог `public`,
чтобы сборка не падала на clean server checkout, где пустая папка не хранится
в git. Для ускорения повторных rebuild на слабых серверах build stage теперь
использует именованные BuildKit cache mounts для `/root/.npm` и `.next/cache`,
внутрь `next build` дополнительно включён `NODE_COMPILE_CACHE`, а `.dockerignore`
держит вне build context документацию и proxy-конфиги, не нужные для standalone image.
Telemetry Next.js отключена, а production build пропускает ESLint через `next.config.js`
(`eslint.ignoreDuringBuilds = true`). TypeScript typecheck и сам `next build`
сохраняются. Дополнительно install stage переведён в low-memory режим:
`npm ci` запускается с отключёнными `audit/fund/progress`, с
`NPM_CONFIG_MAXSOCKETS=1` и с ограничением heap
`NODE_OPTIONS=--max-old-space-size=384`, чтобы weak VPS не убивал build на
шаге установки зависимостей с `exit 137`.

## Deployment modes

### 1. Local stack (`admin`)

- контейнерный порт `3000`, только внутри `modelline_net`
- наружу не публикуется
- browser-facing вход даёт nginx из `microservice_infra`: `http://localhost:8501/admin/`
- используется при обычном полном локальном запуске платформы

### 2. Online head (`admin-online`)

- отдельная online-head пара под profile `online`: внутренний `admin-online` + browser-facing `admin-online-proxy`
- `admin-online` остаётся на внутреннем `3000`, а `admin-online-proxy` публикует `80/443` на своей машине по умолчанию (`ADMIN_HTTP_PORT` / `ADMIN_HTTPS_PORT`)
- не требует `modelline_net`
- используется в split deployment, когда backend-хост поднят в режиме `noadmin`, а admin живёт отдельно
- внешние адреса берутся из namespace `ONLINE_*`: `ONLINE_KAFKA_BOOTSTRAP_SERVERS`, `ONLINE_REDPANDA_ADMIN_URL`, `ONLINE_ACCOUNT_URL`, `ONLINE_GATEWAY_URL`, `ONLINE_MINIO_URL`
- для split deployment эти `ONLINE_*` должны указывать на backend host/domain, а не на `localhost`

Канонические browser URL в этом режиме: `https://sha-trade.tech/admin/` и
`https://www.sha-trade.tech/admin/`.
`admin-online` работает с `basePath=/admin`, поэтому bare `https://sha-trade.tech/`
не должен считаться правильной точкой входа. Если backend-хост запущен в
режиме `noadmin`, то его собственный `8501` тоже не является адресом панели:
UI живёт только на отдельном admin-host.

`admin-online-proxy` принимает оба browser-facing порта:

- `80` — redirect на `https://$host/...`
- `443` — TLS termination + proxy `/admin/*` → `admin-online:3000`

По умолчанию proxy читает домены и сертификат из runtime env:

- `ADMIN_PRIMARY_DOMAIN=sha-trade.tech`
- `ADMIN_SECONDARY_DOMAIN=www.sha-trade.tech`
- `ADMIN_TLS_CERT_PATH=/etc/letsencrypt/live/sha-trade.tech/fullchain.pem`
- `ADMIN_TLS_KEY_PATH=/etc/letsencrypt/live/sha-trade.tech/privkey.pem`

То есть отдельный внешний reverse proxy для production-onlyadmin теперь не
нужен: browser-facing TLS завершает сам compose-сервис `admin-online-proxy`.

Практически это означает следующее: если admin-head живёт на одном сервере, а backend на другом, пустые `ONLINE_*` оставлять нельзя. Иначе `admin-online` будет пытаться ходить в локальные `localhost:*`, а dashboard покажет `fetch failed` / `unreachable`. Для published backend ports дефолты у `admin-online` такие:

- `ONLINE_ACCOUNT_URL` → `localhost:7510`
- `ONLINE_GATEWAY_URL` → `localhost:7520`

Но при реальном split deployment их нужно переопределять на адрес backend-хоста, например `95.165.27.159:7510` и `95.165.27.159:7520`.

Для launcher-сценария это больше не нужно делать вручную по одному ключу. `microservicestarter` в режиме `onlyadmin` принимает один backend host/IP аргументом или спрашивает его интерактивно, затем сохраняет `ONLINE_BACKEND_HOST`, автоматически заполняет derived `ONLINE_*` в `microservice_admin/.env`, предлагает `ADMIN_BACKEND_BASE_URL` с default `https://<host>:8443`, при пустых ключах дописывает `ADMIN_HTTP_PORT` / `ADMIN_HTTPS_PORT`, `ADMIN_PRIMARY_DOMAIN` / `ADMIN_SECONDARY_DOMAIN`, `ADMIN_TLS_CERT_PATH` / `ADMIN_TLS_KEY_PATH`. Доступ в панель выполняется через `/login`: допускается только Account Service пользователь с ролью `admin`, а его access/refresh tokens хранятся в httpOnly cookies и admin JWT пересылается в gateway facade. Если `microservice_account` не получил явный `ADMIN_BOOTSTRAP_*`, первый старт создаёт дефолтного admin-пользователя `admin/admin`, и форма входа принимает username или email.

Admin login теперь кэшируется на стороне браузера через пару `access + refresh` cookies. Middleware не показывает защищённые страницы и admin API без admin-сессии, а если access token истёк, но refresh token ещё жив, сначала делает silent `POST /api/account/refresh`, обновляет cookies и только потом пропускает запрос дальше. Из-за этого повторный заход в панель обычно не показывает `/login` до истечения refresh token; сам `/login` при уже живой или refreshable admin-сессии редиректит обратно в панель.

Если backend facade отвечает `401` или `403`, `admin-online` теперь трактует
это как отсутствующую, истёкшую или не-admin сессию. `/api/kafka` прокидывает
HTTP status, `code`, `detail` и `correlationId` в браузер; общий ключ
между admin-host и backend-host больше не используется.
Если Node runtime admin-host не доверяет self-signed сертификату backend,
`/api/kafka` отдаёт отдельный код `admin_backend_tls_untrusted` с причиной
вроде `DEPTH_ZERO_SELF_SIGNED_CERT`; для autogenerated backend cert включи
`ADMIN_BACKEND_TLS_INSECURE=1` и перезапусти `admin-online`.
Для разбора live-подключения смотри container logs `admin-online`: route
печатает этапы с тегами `[api/health]`, `[api/kafka]` и `[admin-backend]`.
Эти логи показывают runtime env-ветку, backend URL, TLS flag, topic/path,
HTTP status, `code`, `detail`, duration и `correlationId`, но не печатают
bearer token.

В UI те же admin-side diagnostics доступны на странице **Logs** — шестым
пунктом левого меню после Anomaly. Страница читает in-memory runtime buffer
через `GET /api/logs`, умеет запускать быстрый connectivity check
(`/api/health`, `cmd.data.health`, `cmd.analytics.health`) и показывает
последние события с source/event/level/details. Буфер process-local,
очищается при рестарте `admin-online` и не заменяет `docker compose logs`,
но даёт оператору быстрый просмотр текущей admin-side цепочки прямо из
ModelLine без доступа к shell.

Детальная живая очередь вынесена в отдельную страницу **Queue** — седьмой
пункт меню после Logs. Она совмещает `DatasetJobsPanel` и отдельную
**queue history**, а не общий runtime request-stream. История берётся из
`/api/queue/history`: `POST /api/kafka` сохраняет туда крупные
queue-операции (`jobs.start/cancel`, delete/clean/export, repair/recompute,
train/anomaly run) с кратким payload/response summary, duration,
`correlationId` и, если применимо, выбранной биржей. Дополнительно
terminal dataset-job completions best-effort дописываются туда через
`POST /api/queue/history`, чтобы failed/canceled jobs можно было быстро
убирать из live-панели, не теряя историю завершения. Источник хранится
отдельно от `/api/logs`, переживает reload страницы и restart admin-контейнера,
потому что пишется в SQLite (`SQLITE_DB_PATH`, по умолчанию
`/app/.runtime-data/admin-state.sqlite`), поэтому Queue показывает именно
историю очереди, а Logs остаётся общим операторским trace.
`DatasetJobsPanel` теперь не смешивает один backend percent для всего сразу:
для dataset jobs он рисует отдельный current-stage bar (`stage_progress` /
`stage_total/completed`) и отдельный overall bar (`overall_progress`).
Если backend stage не умеет честно дать счётчик, stage-bar остаётся
живым/indeterminate вместо того, чтобы врать pipeline-процентом.
Ping/read-only шум (`jobs.list`, `jobs.get`, health-check'и, polling) по-прежнему
остаётся только в Logs. Download-экран после этого оставляет у себя только
компактный `AllIngestProgress`, без верхнего списка всех jobs.

`market_watch` больше не считается queue-job для оператора. Queue History
теперь намеренно игнорирует legacy `market_watch` completions и watcher
control topics, а `Duration` для terminal dataset jobs считается как реальное
время между `started_at` и `finished_at`, то есть `running → finished`, а не
как round-trip отдельного admin-запроса.

Для live-market overlay добавлена отдельная страница **Market Watcher** —
восьмой пункт меню после Queue. Она читает только dedicated Kafka topics
`cmd.data.market_watcher.{status,set_enabled,rows,logs}` и показывает:

- runtime status (`desired/effective`, heartbeat, flush, last error)
- on/off control без перезапуска сервиса
- realtime prices по всем tracked symbols
- lag от реального времени по каждой строке и summary lag cards
- watcher-only logs, отделённые и от `/logs`, и от Queue History

Dataset jobs на странице Download теперь страхуются не только SSE: пока
экран отслеживает активные ingest jobs, admin дополнительно делает
best-effort polling `cmd.data.dataset.jobs.list` + `cmd.data.dataset.jobs.get`
раз в 5 с. Это устраняет ложный UI-сценарий, когда backend уже перевёл job
в `running` или terminal-state, но browser пропустил progress/completed
event и продолжал показывать `queued` / `stalled`.
Дополнительно store теперь сам доразрешает пропавшие active jobs через
`cmd.data.dataset.jobs.get`: если job исчезла из `active`, а completed-event
не был доставлен, admin подтягивает её terminal-state и убирает зависший
`running/queued` без ручного refresh страницы. `failed/canceled` live jobs
автоскрываются через 10 с, а ошибки Download-ингеста параллельно пишутся в
локальный Action History страницы, чтобы transient error не распирал layout,
но оператор не терял контекст.

На самой странице Queue live-sync сделан агрессивнее, чем на остальных
экранах: `useDatasetJobsFeed` там крутит active-jobs refresh каждые `1.5 s`
и дополнительно триггерится на `window.focus` / `visibilitychange`, поэтому
operator view обновляет queued/running/finished почти сразу после смены
состояния, без ощущения «очередь замерла на 5 секунд».

`AllIngestProgress` на Download синхронизирован с backend runner по
ёмкости ingest-очереди и теперь отображает 4 execution slot-а вместо 2.

Backend-host теперь сам поднимает `:8443` без ручной подготовки `tls.crt` / `tls.key`: `microservice_infra` автогенерирует self-signed сертификат в `ADMIN_BACKEND_CERTS_DIR`, если каталог пустой. Чтобы этот split-path работал без дополнительного trust-store bootstrap, `admin-online` по умолчанию получает `ADMIN_BACKEND_TLS_INSECURE=1` и принимает self-signed backend cert. `/api/health` вычисляет split-mode из runtime env на каждый запрос и в split-mode сначала проверяет `ADMIN_BACKEND_BASE_URL/health/ready`, а если backend facade ещё не публикует этот маршрут и отвечает `404`, автоматически откатывается на legacy `ADMIN_BACKEND_BASE_URL/health`. Это сохраняет совместимость со старыми backend-host deploy-ами и одновременно убирает ложный сценарий «gateway HTTP жив, но Kafka path мёртв, а admin всё равно показывает online» после обновления infra facade. После установки доверенного сертификата на backend-host переведи admin-host на `ADMIN_BACKEND_TLS_INSECURE=0`.

Dashboard на главной странице теперь явно показывает, к какому backend host/IP подключён текущий admin, отдельным заметным connection-блоком над stat cards. Источник один и предсказуемый: compose кладёт в runtime `BACKEND_CONNECTION_TARGET`, где local stack всегда показывает `localhost`, а `admin-online` берёт значение из `ONLINE_BACKEND_HOST`. Тот же блок теперь показывает и реальный `KAFKA_BOOTSTRAP_SERVERS`, который использует admin, плюс текст ошибки broker connectivity, если Kafka path недоступен. Дополнительно `connectionTarget` дублируется в верхней строке dashboard, в sidebar header под логотипом и в footer sidebar, чтобы оператор видел target backend на любой странице admin-панели.

Dataset-секция Dashboard теперь фильтруется по бирже через dropdown прямо в header страницы. Фильтр строится только из реально доступных бирж, для которых уже есть непустые таблицы, и влияет на `Coverage`, список `Tables` и dataset stat cards на главной. Таблицы с `0 rows` больше не попадают в `Coverage`, не рендерятся в `Tables` и не участвуют в `Total Tables`; сам coverage-chart стал выше и теперь масштабируется по числу видимых таблиц, поэтому при живых multi-exchange данных его проще читать.

В обоих режимах admin остаётся Kafka-driven UI-слоем без собственного job-runner'а.
Даже новый Market Watcher page только управляет и визуализирует удалённый
runtime во владельце данных (`microservice_data`) и не исполняет watcher внутри себя.

## Документация для агентов

- [STRUCTURE.md](STRUCTURE.md) — карта страниц, API routes, hooks и shared UI-модулей
- [../docs/agents/services/microservice_admin.md](../docs/agents/services/microservice_admin.md) — профиль сервиса для agent workflow
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first маршрут работы

## SQLite State Layer

Страницы кешируют результаты дорогих Kafka-запросов и persistent UI state в
SQLite-backed store, чтобы UI мгновенно отображал предыдущие данные при
перезагрузке страницы и не терял queue history / выбранные dataset-параметры
после restart admin-контейнера.

### Компоненты

| Файл | Описание |
| ---- | -------- |
| `src/lib/sqliteStore.ts` | **Server-only.** Встроенный Node `node:sqlite` store. Держит таблицы `kv_store` и `queue_history`, уважает TTL для cached key/value и ограничивает queue history последними 400 записями. Путь задаётся через `SQLITE_DB_PATH`. |
| `src/lib/cacheClient.ts` | **Browser-safe.** `cacheRead<T>(key)` и `cacheWrite(key, value, ttl)` общаются с `/api/cache` через `fetch`; браузер не ходит к SQLite напрямую. |
| `src/app/api/cache/route.ts` | Route Handler. GET `?key=X` → `{value}`. POST `{key,value,ttl?}` → `{ok:true}`. Серверный bridge к `sqliteStore.ts`. |
| `src/lib/adminRuntimeLog.ts` | **Server-only.** Process-local ring buffer последних admin runtime diagnostics. Используется `/api/health`, `/api/kafka` и `backendClient.ts`, чтобы страница Logs могла показать те же stage/source/event/status/code/detail/correlationId, которые печатаются в container stdout. Bearer token и payload не пишутся. |
| `src/lib/queueHistoryStore.ts` | **Server-only.** Отдельный persistent ring buffer истории очереди поверх `sqliteStore.ts`. Хранит только крупные queue-операции, которые `/api/kafka` завершил success/error, и переживает restart admin-process/container, пока жив `SQLITE_DB_PATH`. |

### Переменная окружения

| Переменная | Описание |
| ---------- | -------- |
| `SQLITE_DB_PATH` | Путь к admin SQLite state. В compose по умолчанию `/app/.runtime-data/admin-state.sqlite`; каталог монтируется в named volume, поэтому queue history и cached UI state переживают restart контейнера. |

### Ключи и TTL

| Страница | Ключ | TTL | Что кешируется |
| -------- | ---- | --- | -------------- |
| Dashboard | `modelline:dashboard:v1` | 60 мин | `tables`, `coverage`, `modelCount` |
| Anomaly | `modelline:anomaly:v1:{symbol}:{timeframe}` | 30 мин | `stats`, `coverage` |
| Download | `modelline:params:dataset` | 5 лет | выбранные `symbol`, `timeframe`, `dateFrom`, `dateTo`, `exchange` |
| Download | `modelline:dataset-tables:v1` | 60 мин | список таблиц (`DataTableInfo[]`) |
| Download | `modelline:dataset-coverage:v1:{symbol}:{timeframe}` | 30 мин | coverage для одного TF |
| Download | `modelline:dataset-allcoverage:v1:{symbol}` | 30 мин | coverage по всем TF (`AllCoverageItem[]`) |

### Паттерн

1. На маунте / смене параметров — `cacheRead` (если есть → мгновенно отображаем).
2. Параллельно / следом — Kafka-запрос за свежими данными.
3. После Kafka-ответа — `cacheWrite` fire-and-forget.

Здоровье сервисов (health-чеки), гистограммы и browse-строки **не** кешируются.

## Архитектура запросов

```text
Browser → Next.js page (client component)
             ↓ fetch (command)
          POST /api/kafka          (Next.js Route Handler, server-side)
             ↓ kafkajs request/reply
          Redpanda broker
             ↓
          microservice_data | microservice_analitic

Browser → Next.js page (client component)
             ↓ EventSource
          GET /api/events           (Next.js Route Handler, SSE stream)
             ↓ kafkajs consumer (EVT_* topics)
          Redpanda broker ← microservice_analitic (events)

Browser → Next.js page (client component)
             ↓ fetch
          GET /api/health           (Next.js Route Handler, server-side)
             ↓ HTTP (fetch, timeout 2 с, Promise.allSettled)
          redpanda admin | minio | account | gateway  ← внешние health probes, НЕ application IPC
```

Клиентский код использует `kafkaCall()` из `src/lib/kafkaClient.ts` для команд (POST /api/kafka).
Для event-driven обновлений используется `useEvents()` хук, который открывает `EventSource('/api/events')`.
Health application-сервисов (`data`, `analitic`) — через Kafka (`cmd.*.health`), не через HTTP.
`fetchInfraHealth()` из `src/lib/healthClient.ts` используется для server-side HTTP
probe Redpanda admin, MinIO, account и gateway. В local stack эти адреса обычно
резолвятся по docker-hostname внутри `modelline_net`; в online-head режиме — через
`ONLINE_*` namespace. В split mode route пробует только backend facade
`ADMIN_BACKEND_BASE_URL/health`, но всё равно возвращает полный
`InfraHealthResponse` с `kafka.bootstrapServers` и согласованными статусами
`online/offline` для dashboard. Прямого доступа браузера к Kafka нет.

Для split deployment есть отдельный runtime toggle: `ADMIN_BACKEND_TLS_INSECURE`.
Если он равен `1`, server-side Next runtime выставляет
`NODE_TLS_REJECT_UNAUTHORIZED=0` до backend fetch-ов и тем самым принимает
self-signed сертификат backend facade. Это рассчитано именно на autogenerated
cert из `microservice_infra`; при trusted cert оставляй `0`.

### KafkaJS 2.x + Redpanda workaround

KafkaJS 2.x шлёт `MetadataRequest v6` с флагом `auto-create` при `consumer.subscribe()`. Redpanda
на этот запрос отвечает `INVALID_PARTITIONS` для несуществующего топика, и KafkaJS падает с
`Error: Number of partitions is invalid`.

Тот же паттерн применяется в двух местах — `src/lib/kafka.ts` (process-wide reply-inbox
для request-reply) и `src/lib/sseHub.ts` (process-wide consumer для SSE):

1. Consumer создаётся с `allowAutoTopicCreation: false` — KafkaJS не просит брокера авто-создавать топик.
2. Перед `consumer.connect()` все нужные топики явно создаются через
   `admin.createTopics({ topics: [...], waitForLeaders: false })`
   (`numPartitions: 1`, `replicationFactor: 1`). `TOPIC_ALREADY_EXISTS` (код 36)
   тихо игнорируется как идемпотентный, прочие ошибки логируются через `console.warn`.
3. Топик создаётся **один раз на жизнь процесса** (а не на каждый запрос),
   поэтому 500-мс пауза для leader election теперь не нужна — она амортизируется
   на тысячи запросов вместо одного.

### Long-lived reply-inbox + SSE hub (производительность)

**Было.** `kafkaRequest()` в каждом вызове создавал уникальный `reply.<svc>.<uuid>`
топик, поднимал отдельный Kafka consumer + group, ждал 500 мс leader election,
а в `finally` пытался удалить топик. SSE делал то же самое: каждое открытое
окно браузера открывало отдельный Kafka consumer с уникальным `groupId`. Это:

- добавляло ~700 мс латентности на каждый Kafka-запрос,
- создавало десятки одноразовых топиков в минуту → потребовало 30-минутный
  janitor для подметания мусора,
- при 10 открытых вкладках admin'а Redpanda держала 10 consumer-групп для
  одного и того же набора `EVT_*` событий.

**Стало.** Каждый Admin-процесс владеет:

| Объект | Файл | Лайфтайм |
| ------ | ---- | -------- |
| **Reply-inbox** `reply.microservice_admin.<instance>` | `src/lib/kafka.ts` | весь процесс |
| **EVT_* consumer** group `admin-sse` | `src/lib/sseHub.ts` | весь процесс |

`kafkaRequest()` теперь = `producer.send` + `await pending future`. Цикл консьюмера
матчит входящие envelopes по `correlation_id` через `Map<string, TaskCompletionSource>`
и резолвит ожидающего вызывающего. Локально латентность падает с ~700 мс до < 50 мс.

SSE-хаб (`src/lib/sseHub.ts`) держит **один** Kafka-consumer на процесс, fan-out на
всех подключённых браузеров через `Set<Subscriber>`. `/api/events` route просто
регистрирует callback и возвращает SSE-стрим; новых Kafka-объектов на вкладку
не создаётся. Heartbeat `:keepalive` каждые 25 с защищает от idle-таймаутов прокси.

### Request coalescing

`src/lib/kafkaCoalesce.ts` оборачивает `/api/kafka` коротким TTL-кэшем для
read-only summary-запросов (health, list_tables, coverage, dataset.status,
constants, model.list). Несколько компонентов, монтирующиеся одновременно и
запрашивающие один и тот же топик с одинаковым payload, получают **один**
Kafka-roundtrip на всех. Mutating-команды (ingest, clean, train, anomaly) не
коалесцируются — payload-сличение по stable JSON; коалесинг отключается, если
вызывающий передал собственный `correlationId` (значит, он подписан на progress).

| Топик | TTL |
| ----- | --- |
| `cmd.data.health`, `cmd.analytics.health`, `cmd.data.db.ping` | 1.5 c |
| `cmd.data.dataset.list_tables`, `cmd.data.dataset.coverage`, `cmd.analitic.dataset.status` | 2 c |
| `cmd.data.dataset.table_schema` | 10 c |
| `cmd.analytics.model.list` | 5 c |
| `cmd.data.dataset.constants` | 30 c |

## Design System (shadcn/ui)

**CSS variables** (`globals.css` `@layer base :root {}`):

- `--background: 222 47% 7%` — фон приложения
- `--card: 222 47% 16%` — фон карточек и сайдбара (повышена яркость для контраста)
- `--primary: 217 91% 60%` — акцентный синий
- `--muted: 217 33% 20%` — muted bg (TabsList, input bg)
- `--muted-foreground: 215 20% 65%` — вторичный текст
- `--border: 217 33% 22%` — границы
- `--accent: 217 33% 25%` — hover/accent фон
- `--success: 142 71% 45%` / `--destructive: 0 84% 60%` / `--warning: 38 92% 50%`

**Fluid typography (`--font-size-*`):** CSS custom properties с `clamp()`, минимум при 360 px, максимум при 2560 px (Δ = 2200 px). Семь ступеней: `xs` / `sm` / `base` / `lg` / `xl` / `2xl` / `3xl`. Переопределяют Tailwind-утилиты `.text-*` через `@layer utilities` (более поздний source-order), поэтому на узких экранах (portrait/phone) текст автоматически сжимается, а на 4K — растягивается.

**Шрифт:** Inter (загружен через `next/font/google`)

**Брейкпоинты:**

- Стандартные Tailwind: `sm` 640 px, `md` 768 px, `lg` 1024 px, `xl` 1280 px, `2xl` 1536 px
- Кастомные: `xs` 480 px (phone landscape / small portrait), `3xl` 1920 px (Full HD), `4xl` 2560 px (4K)

**Адаптивный дизайн (20:9 → 9:20):**

- **Root layout** (`src/app/layout.tsx`): `flex flex-col md:flex-row` — на `< md` сайдбар становится нижней навигацией (`order-last`), на `md+` возвращается слева. `<main>` имеет `pb-14 md:pb-5 lg:pb-6` чтобы контент не прятался под bottom-nav. Контент ограничен `max-w-[1920px]` только с `md:`, на узких — `max-w-full`.
- **Sidebar** (`src/components/Sidebar.tsx`): три режима по `window.innerWidth`:
  - `≥ 1024 px` — **expanded-collapsible**: full label `w-56` с toggle-кнопкой; свёрнутое состояние `w-14` (только иконки) сохраняется в `localStorage('sidebar:collapsed')`.
  - `768 – 1023 px` — **icon-only**: всегда `w-14` (иконки + tooltip), toggle-кнопка скрыта, `localStorage` игнорируется.
  - `< 768 px` — **bottom-nav**: горизонтальная полоса `h-14 border-t` снизу экрана, пункты flex-row, показывается только иконка с `aria-label` (текст скрыт для экономии места). Режим определяется в `useEffect` + `resize` listener.
- **CSS variables**: `--sidebar-width` в `globals.css` — `0px` на мобилке, `3.5rem` с `md`, `14rem` с `lg`. Используется для страниц, которым нужно знать текущую ширину сайдбара.
- **Fluid padding**: контейнеры страниц используют `gap-4 sm:gap-6`, `p-3 sm:p-4`, сетки — `grid-cols-1 xs:grid-cols-2 md:grid-cols-4`.
- **Таблицы**: все `<Table>` обёрнуты в `<div className="overflow-x-auto">` чтобы на узких экранах появлялся горизонтальный скролл.
- **Контролы**: на `< xs` Select/Button/Input растягиваются на всю ширину (`w-full xs:w-auto`, `min-w-0 flex-1 xs:min-w-[180px]`).

**Анимации:**

- `pulse-dot` — пульсирующая точка (`.status-dot-ok` класс) для Kafka-статуса
- `shimmer` — скользящий блик, используется в skeleton

## shadcn/ui Components (`src/components/ui/`)

| Файл | Описание |
| ---- | -------- |
| `button.tsx` | CVA variants: default/outline/secondary/ghost/link; sizes: default/sm/lg/icon |
| `card.tsx` | Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter |
| `badge.tsx` | CVA variants: default/secondary/destructive/outline/success/warning/info |
| `skeleton.tsx` | Animate-pulse placeholder |
| `progress.tsx` | Radix `@radix-ui/react-progress` wrapper |
| `table.tsx` | Table, TableHeader, TableBody, TableRow, TableHead, TableCell |
| `tabs.tsx` | Radix `@radix-ui/react-tabs`: Tabs, TabsList, TabsTrigger, TabsContent |
| `select.tsx` | Radix `@radix-ui/react-select`: Select, SelectTrigger, SelectContent, SelectItem, SelectValue |
| `separator.tsx` | Radix `@radix-ui/react-separator` |
| `tooltip.tsx` | Radix `@radix-ui/react-tooltip`: Tooltip, TooltipTrigger, TooltipContent, TooltipProvider |
| `input.tsx` | Стилизованный `<input>` |

**Утилита:** `src/lib/utils.ts` — `cn(...inputs)` = `clsx` + `tailwind-merge`

## Производительность

**Stale-while-revalidate:** каждая секция Dashboard обновляется независимо. Kafka-запросы запускаются параллельно без `await`; каждый обновляет свой срез состояния по завершении. UI никогда не блокируется ожиданием самого медленного запроса.

**Таймауты:**

- Health-чеки: `2 000 мс`
- Список таблиц: `8 000 мс`
- Coverage: `5 000 мс`

## SSE Event-Driven обновления

Для избежания polling-нагрузки Dashboard и Train страницы получают обновления через SSE:

| SSE событие | Топик Kafka | Кто подписан | Действие |
| ----------- | ----------- | ------------ | -------- |
| `events.analytics.model.ready` | `EVT_ANALYTICS_MODEL_READY` | Dashboard | Перезапрашивает список моделей (`modelCount`) |
| `events.analytics.train.progress` | `EVT_ANALYTICS_TRAIN_PROGRESS` | Train page | Добавляет точку в `progressHistory`, обновляет `status.progress` |

**Компоненты:**

- `GET /api/events` — SSE Route Handler. Подписывается на process-wide SSE-хаб (`src/lib/sseHub.ts`); каждое новое соединение лишь добавляет subscriber-callback в `Set<Subscriber>`. Один Kafka-consumer на процесс, fan-out всем подключённым клиентам. Heartbeat `:keepalive` каждые 25 с.
- `src/hooks/useEvents.ts` — React hook `useEvents(handlers)`. Открывает `EventSource` на mount, диспатчит payload в нужный handler, закрывает на unmount. Handlers хранятся в ref — не вызывает реконнект при перерендере.

## Компоненты UI

- **`src/components/Sidebar.tsx`** — сворачиваемая боковая навигация. Два состояния: `w-56` (развёрнут) / `w-14` (свёрнут), анимировано через `transition-all duration-200`. Кнопка ChevronLeft/Right для переключения; состояние сохраняется в `localStorage` (`modelline:sidebar:collapsed`). Логотип с lucide-react `Zap`. При развёрнутом sidebar — пульсирующий dot Kafka-статуса. Nav-иконки показывают `title` tooltip при свёрнутом состоянии. Kafka healthcheck на mount, повторяется каждые 30 с. Шестой пункт меню — Logs (`/logs`, иконка `ScrollText`).
- **`src/components/Toast.tsx`** — глобальные toast-уведомления. Экспортирует хук `useToast()` и провайдер `ToastProvider`. Типы: `success`, `error`, `info`. Автозакрытие через 4 с.

## Страницы

| Страница | Описание |
| -------- | -------- |
| `/` (Dashboard) | Bento Grid: Row 1 — 4 StatCard с `border-l-4 border-l-{color}` акцентами (`grid-cols-2 xl:grid-cols-4`) и exchange dropdown в header. Dataset-related stat cards (`Total Tables`, `Total Rows`, `Last Ingestion`) теперь считаются по текущему dashboard exchange-filter и игнорируют таблицы с `0 rows`. Row 2 — 2 колонки: стек из service cards слева и более высокий `CoverageBar` справа; chart строится только по непустым таблицам выбранной биржи. Row 3 — shadcn Table датасетов с `pct.toFixed(1)%`, тоже отфильтрованная по выбранной бирже и без zero-row строк. Кнопка Refresh обновляет все карточки одновременно. |
| `/download` | 2-колоночный layout (`lg:grid-cols-[380px,1fr]`): слева Dataset Configuration, ingest controls и quality actions; справа coverage/stat cards; ниже Available Tables, Quality Block и Action History. Кнопка Ingest идёт в одной строке с exchange selector (`ALL`, `Bybit`, `Binance`, `Kraken`), а выбранные `symbol/timeframe/date-range/exchange` сохраняются и в `localStorage`, и в server-side SQLite store (`modelline:params:dataset`), поэтому переживают reload страницы и restart admin-контейнера. И `Symbol`, и `Exchange` теперь поддерживают `ALL`: admin не создаёт special multi-table contract, а fan-out'ит выбор в набор конкретных dataset jobs по full scope `exchange::symbol::timeframe`. `Symbol=ALL` по-прежнему принудительно держит `timeframe=ALL`; `Exchange=ALL` можно комбинировать с конкретным timeframe или с `ALL`. Coverage/export/repair/delete для aggregate-режимов с `ALL` отключены, потому что эти операции адресуют одну таблицу. Ingest работает только через dataset jobs: и single-TF, и `ALL` сначала делают `refreshCoverageState()` без обнуления покрытия, затем отправляют быстрый `CMD_DATA_DATASET_JOBS_START` (`timeoutMs: 5_000`). После успешного ответа timeframe попадает в `queued` и seed-ится через `seedQueuedJob(...)`; в `running` он переходит только после реального backend-progress/job update. Локальный `loading/busy` и page-level lock не снимаются сразу после `JOBS_START`: admin остаётся занятым, пока принятая remote job действительно не перейдёт в terminal state; если job не создана, busy-state отпускается сразу по явному backend-отказу. `ALL`-виджет `AllIngestProgress` показывает 4 execution slot-а, отдельную очередь queued jobs, stalled-state если очередь есть, а running нет слишком долго, и recent done/error list. Для running-slot видны stage, progress, detail, elapsed и short job id. Успешное завершение с `completed=0` показывается как нормальный no-op (`без новых строк` / `дозагрузка не потребовалась`), а не как скрытая ошибка. Failed TF rows и terminal error cards автоубираются через 10 секунд, а ошибка параллельно пишется в локальный Action History, поэтому экран не забивается старыми сообщениями. Backend start errors (`schema_not_ready`, `bad_request`, `unsupported_exchange`, `db_unavailable`, `pg_*`, `internal_error`) сразу переводят конкретный TF в `error`. Backend теперь реально исполняет ingest для `bybit`, `binance` и `kraken`; canonical table naming стал exchange-aware (`btcusdt_1m` для legacy Bybit, `binance_btcusdt_1m` / `kraken_btcusdt_1m` для остальных бирж). Coverage cache, export и queue seed учитывают выбранный `exchange`, поэтому новые строки не путаются между биржами. **Dataset CSV/ZIP export — zero-byte для admin**: route `/api/export/csv` только проксирует Kafka-ответ `{ presigned_url }` и пробрасывает URL клиенту как есть, без host-нормализации и без legacy raw-localhost fallback; `timeframe=ALL` теперь тоже собирается из exchange-aware table names. URL уже подписан data-сервисом на browser-facing origin (внешний вход infra-nginx, по умолчанию `http://localhost:8501`), а `/modelline-blobs/*` стримит файл из MinIO напрямую — байты не проходят через admin runtime, поэтому файлы значительно больше 2 ГБ работают штатно. Quality audit и repair-actions теперь работают для любой из трёх бирж: `load_ohlcv` по-прежнему инициируется через `cmd.analitic.dataset.load_ohlcv`, но analytic-сервис больше не ходит в биржу сам, а делегирует exchange-aware `cmd.data.dataset.repair_ohlcv` владельцу данных; `recompute_features` аналогично резолвит exchange-aware table name. Delete rows и ingest queue по-прежнему идут через Kafka-команды владельцам сервисов: admin только инициирует операции и отображает удалённое состояние. |
| `/train` | Кастомный tab-switcher в header (без Radix Tabs). 2-колоночный grid на `lg+`: левая — Config + Status Card с `ProgressLine` (recharts LineChart, показывается после ≥2 точек прогресса); правая — Training History table. `progressHistory` state сбрасывается при каждом новом запуске. |
| `/compare` | CSS grid 2 колонки, shadcn Card в каждой, shadcn Select, Button экспорта |
| `/anomaly` | Инспекция / ML-детекция / очистка / экспорт датасетов. **Detection parameters**: 4 inline-секции c чекбоксами и параметрами для новых типов — Rolling Z-score/IQR (window/threshold/mode), Frozen/stale price (min consecutive), Return outlier (threshold %), Volume/turnover mismatch (tolerance %). **Inspect**: df.info-таблица + lazy-гистограммы. **Browse**: постраничный просмотр сырых строк + per-column charts. **Anomalies секция** (новый layout): summary-карточки + Smart Suggestions panel (ранжированный по severity список рекомендаций с inline кнопкой "Apply" — мгновенно включает соответствующий clean checkbox и запускает Apply) + Tabs: **Timeline** (scatter chart с категориальной Y-осью, цвет точек = severity), **Table** (paginated detail с фильтрами), **DBSCAN** (eps/min_samples/max_sample_rows), **IForest** (Isolation Forest: contamination/n_estimators/max_sample_rows через `cmd.analitic.anomaly.isolation_forest`), **Distribution** (skewness, excess kurtosis, JB p-value + histogram log-returns с N(μ,σ)-overlay через `cmd.analitic.dataset.distribution`), **History** (lazy-load `cmd.data.dataset.audit_log` — все clean.apply записи; кнопка Rollback зарезервирована, disabled). **Clean**: 5 операций в карточках с inline-параметрами при checked — drop_duplicates имеет strategy (first/last/none), fill_zero_streaks — columns selector (all/volume/open_interest/funding_rate), fill_gaps — method (forward_fill/linear/drop_rows). **Export**: dropdown в header с выбором формата (CSV/JSON) и subset (all/critical/dbscan/iforest); скачивание через Blob+`URL.createObjectURL` без backend, файл `anomaly_report_{symbol}_{tf}_{ts}.{ext}`. **Session badge** (Analyze авто-загружает сессию через `cmd.analitic.dataset.load`/`status`, Unload — `cmd.analitic.dataset.unload`). **SQLite-backed cache**: stats+coverage+anomalies восстанавливаются при смене symbol/timeframe; ML-детектора, distribution, audit log не кешируются. |
| `/logs` | Operator Logs: читает `GET /api/logs?limit=250`, автообновляется каждые 5 с, показывает counters Total/Warnings/Errors и таблицу runtime events. `Run Check` вызывает `/api/health`, `cmd.data.health`, `cmd.analytics.health`; `Clear` очищает process-local буфер через `DELETE /api/logs`. |

## Roadmap

### Phase 3 — Performance & contracts (latest sweep)

- **`list_tables` без N+1.** DataService уже отдаёт обогащённые записи
  `{ table_name, rows, coverage_pct, date_from, date_to }` за один Kafka
  round-trip. Dashboard (`src/app/page.tsx`) и Download (`src/app/download/page.tsx`)
  больше **не** запускают `Promise.all(tables.map(coverage))`: ответ
  маппится напрямую в строки таблиц, а `min_ts_ms` / `max_ts_ms`
  реконструируются как `Date.parse(\`${date_from}T00:00:00Z\`)` /
  `T23:59:59Z`. Транзитивный fallback на per-table coverage остаётся
  только для legacy-string-элементов (rolling-deploy safety) и должен
  быть удалён, когда все DataService-инстансы будут гарантированно новыми.
- **Browse pagination contract.** Browse-ответ содержит `total_rows`
  (точная цифра, single source of truth) и `total_rows_estimate`
  (информационный, из планировщика PG). UI обязан **закреплять
  `total_rows` на первой странице** и считать `pageCount` от него же —
  использование `total_rows_estimate` в пагинационной математике
  запрещено (приводит к "прыгающему" числу страниц при дрейфе
  оценок планировщика).

- ✅ **Step 1:** skeleton with health dashboard.
- ✅ **Step 1.5:** Kafka-only IPC, HTTP clients deleted.
- ✅ **Step 2:** UI redesign — Sidebar, Toast, utility CSS.
- ✅ **Step 3:** Design system v2 — stale-while-revalidate, Quick Stats.
- ✅ **Step 4:** shadcn/ui overhaul — Radix UI primitives, CSS variable dark theme, full component library, all pages rewritten.
- ✅ **Step 5:** UI redesign v2 — recharts charts (CoverageBar, ProgressLine), bento-grid Dashboard, 2-col layouts, collapsible Sidebar, CSS contrast fixes.
- ⏳ **Step 6:** migrate `frontend/pages/*` here; each former backend call becomes a Kafka command via `services/messaging.py`.
