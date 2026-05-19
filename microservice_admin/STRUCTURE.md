# microservice_admin — Структура

> Обновляй этот файл при каждом изменении компонентов, страниц или библиотечных модулей.

## Связанная документация

- [README.md](README.md) — обзор runtime-поведения, контрактов и runbook сервиса
- [../docs/agents/services/microservice_admin.md](../docs/agents/services/microservice_admin.md) — профиль сервиса для agent workflow
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first маршрут работы

---

## Dataset jobs (Phase G)

Длительные операции датасета теперь не блокируют UI:

- `microservice_admin` не выполняет dataset jobs внутри себя. Он хранит только локальное представление удалённых jobs для UI и отправляет Kafka-команды владельцу job.

- `src/hooks/useDatasetJobs.ts` — process-local store на
  `useSyncExternalStore`. `applyJobProgress` / `applyJobCompleted`
  мутируют `Map<job_id, DatasetJobView>`. `refreshActiveJobs()`
  тянет активные jobs через `CMD_DATA_DATASET_JOBS_LIST` и затем
  доразрешает локально-зависшие active jobs через `CMD_DATA_DATASET_JOBS_GET`,
  если они исчезли из active-list без полученного completed-event.
  `cancelJob(jobId)` отправляет `CMD_DATA_DATASET_JOBS_CANCEL`.
  Завершённые `succeeded/skipped` jobs авто-сворачиваются через 30 с;
  `failed/canceled` остаются до явного `dismissJob`.
  **`seedQueuedJob({jobId, type, target_table})`** — экспортируемый
  helper: после успешного `JOBS_START` UI кладёт job в store со
  статусом `'queued'`, `progress=0`, `finished=false`, чтобы честно
  отразить «в очереди, планировщик ещё не подхватил». Если jobId уже
  в Map — no-op (избегаем гонки с SSE-`progress`, который может
  прилететь раньше).
- `src/components/DatasetJobsPanel.tsx` — компактный список с progress-
  барами, status/stage, error_code/error_message и кнопками
  «Отменить» / «Скрыть». Компонент стилизован в штатный dark/card
  язык admin-панели: без светлого фона и инородных серых вставок,
  с мягкими muted-подложками, status-badge и progress-track в общей
  теме. Для ingest `succeeded` + `completed=0` показывает нормальный
  no-op текст `Новых строк не потребовалось`.
- `src/hooks/useDatasetJobsFeed.ts` — тонкая client-only синхронизация
  dataset jobs store: подписывает страницу на
  `EVT_DATA_DATASET_JOB_PROGRESS` / `...COMPLETED`, делает mount-time
  hydration через `refreshActiveJobs()` и при необходимости добавляет
  polling active jobs. Дополнительно hook теперь дёргает refresh на
  `window.focus` и `document.visibilitychange`, а interval пропускает
  hidden-tab, чтобы Queue-экран обновлял jobs заметно быстрее без лишнего
  background-шума. Используется на `/download` и `/queue`, чтобы live-store
  не зависел от присутствия верхнего jobs-panel на конкретной странице.
- `src/hooks/useEvents.ts` — добавлены кейсы
  `EVT_DATA_DATASET_JOB_PROGRESS` и `EVT_DATA_DATASET_JOB_COMPLETED`,
  пробрасываются в store.
- `src/app/download/page.tsx` — использует `useDatasetJobsFeed()` для
  live dataset jobs sync, но больше не рендерит верхний
  `<DatasetJobsPanel />`: детальная очередь вынесена на `/queue`.
  Межсервисное взаимодействие — только Kafka (HTTP только browser →
  Admin Next.js → Kafka). `JOBS_START` в admin не запускает исполнение
  job локально: он только просит владеющий сервис поставить или
  вернуть job, после чего UI наблюдает за её жизненным циклом.
  **Honest job lifecycle** (Step 4):
  - `TfStatus = 'pending' | 'queued' | 'running' | 'done' | 'error'`.
    `queued` = job создан в DB, scheduler ещё не диспатчил;
    `running` = пришёл первый `evt.data.dataset.job.progress` или
    `job.status === 'running'` из `JOBS_LIST`.
  - `handleIngest` *первым делом* `await refreshCoverageState()`
    (lock-free helper, синхронизирует UI-coverage с реальной БД до
    запуска новых jobs); затем отправляет `JOBS_START`; при успехе
    выставляет `'queued'` и `seedQueuedJob(...)`. Никакого
    `setCoverage(null)`/`setAllCoverages([{rows:0,...}])` —
    существующее покрытие НЕ обнуляется. Если хотя бы одна remote job
    реально создана, `loadingIngest` и page-level `operationLockRef`
    удерживаются до terminal state этих jobs; если backend не создал ни
    одной job, локальный busy-state снимается сразу в `finally`.
  - `refreshCoverageState()` зовётся также из `runRepair` (после
    re-audit) и `runQualityCheck` (success path) и из job-sync
    `useEffect`, когда ALL-job'ы все терминальны или single-TF job
    `succeeded` — coverage всегда отражает актуальное состояние БД.
  - `AllIngestProgress` больше не считает «прогресс по числу TF».
    Виджет строится из remote jobs (`allIngestJobIds` + `useDatasetJobs`)
    и рендерит 4 execution slot-а (или больше, если backend временно
    сообщил больше running jobs, чем штатный cap), размер очереди,
    stalled-state (`queued>0 && running=0` дольше 15 с) и recent
    done/error list. Для running-slot показываются stage, progress,
    detail, elapsed и short job id. Успешный terminal-state с
    `completed=0` показывается как нормальный no-op (`без новых строк`
    / `дозагрузка не потребовалась`). Polling-fallback `hasActive`
    (queued ∪ running) оставлен только для таймера elapsed, пока хоть
    один TF не финализирован.

---

## Корень сервиса

| Файл | Описание |
| ---- | --------- |
| `package.json` | Зависимости: `next@14`, `react@18`, `kafkajs@2`, `ioredis@5`, `uuid@10`, Tailwind CSS 3, shadcn/ui (Radix UI), lucide-react, class-variance-authority, clsx, tailwind-merge, tailwindcss-animate, **recharts ^2.15.0**. `@aws-sdk/client-s3` больше не используется — байты через Admin не проходят, DataService возвращает presigned URL для обоих режимов экспорта. |
| `next.config.js` | Next.js конфиг (App Router, environment proxy). `output: 'standalone'`, `eslint.ignoreDuringBuilds = true`, `basePath: '/admin'`, `assetPrefix: '/admin'`, `env.NEXT_PUBLIC_BASE_PATH: '/admin'`. basePath встраивается в билд — требует пересборки образа при изменении. |
| `tsconfig.json` | TypeScript-конфиг (`@/` → `src/`) |
| `tailwind.config.js` | Tailwind CSS конфиг: `darkMode: ['class']`, shadcn CSS var tokens, keyframes pulse-dot/shimmer/accordion. Кастомные экраны: `xs: '480px'` (phone landscape / small portrait), `3xl: '1920px'` (Full HD), `4xl: '2560px'` (4K) |
| `postcss.config.js` | PostCSS конфиг (CommonJS): регистрирует `tailwindcss` и `autoprefixer` как PostCSS плагины. **Критичен** — без него Next.js не обрабатывает директивы `@tailwind` в `globals.css` и utility-классы не генерируются (CSS bundle ~4 KB вместо ~26 KB). |
| `Dockerfile` | Multi-stage build и runtime на официальном `node:22-bookworm-slim` (Docker Hub); standalone Next.js image с отдельным non-root runtime user. Выбор образа убирает зависимость сборки от `cgr.dev` на Linux-серверах, а build stage принудительно создаёт `public/`, чтобы empty directory не ломала `COPY --from=build /app/public ./public`. Для ускорения повторных rebuild использует BuildKit cache mount для `/root/.npm` и `.next/cache`; telemetry отключена через `NEXT_TELEMETRY_DISABLED=1`. Install stage дополнительно настроен под weak VPS: `npm ci` идёт с `NPM_CONFIG_MAXSOCKETS=1`, без `audit/fund/progress` и с `NODE_OPTIONS=--max-old-space-size=384`, чтобы снизить риск OOM/`exit 137` на установке зависимостей. Runtime stage заранее создаёт `/app/.runtime-data`, потому что туда монтируется persistent SQLite state (`admin-state.sqlite`). |
| `docker-compose.yml` | Поддерживает **два режима**. `admin` — local-stack service: `expose: ["3000"]`, подключён к `modelline_net`, наружу не публикуется, browser-facing вход даёт nginx из `microservice_infra` на `8501/admin/*`. Split profile `online` теперь поднимает `admin-online` (внутренний Next.js на `3000`) и `admin-online-proxy` (nginx TLS proxy). Proxy публикует `${ADMIN_HTTP_PORT:-80}:80` и `${ADMIN_HTTPS_PORT:-443}:443`, делает `301` с bare `/` на `/admin/`, обслуживает `sha-trade.tech` и `www.sha-trade.tech` по умолчанию, монтирует browser-facing cert/key из `ADMIN_TLS_CERT_PATH` / `ADMIN_TLS_KEY_PATH` и проксирует `/admin/*` в `admin-online`. `admin` и `admin-online` оба получают `SQLITE_DB_PATH=/app/.runtime-data/admin-state.sqlite` и отдельный named volume (`admin_sqlite_data` / `admin_online_sqlite_data`), поэтому queue history и cached UI state переживают restart контейнера. `admin-online` не требует `modelline_net`, читает внешние адреса из `ONLINE_*` namespace и прокидывает в контейнер `ADMIN_BACKEND_BASE_URL`, `ADMIN_BACKEND_SHARED_TOKEN` и `ADMIN_BACKEND_TLS_INSECURE` для backend HTTP facade. |
| `nginx/admin-online.conf.template` | Шаблон browser-facing nginx proxy для `onlyadmin`: `80` → redirect на HTTPS, `443` → TLS termination по cert/key с admin-host, домены `sha-trade.tech` и `www.sha-trade.tech`, проксирование `/admin/*` в `admin-online:3000`, SSE `/admin/api/events` без buffering. |

---

## src/app/ (Next.js App Router)

### Корень

| Файл | Описание |
| ---- | --------- |
| `layout.tsx` | Root layout: Inter шрифт, **адаптивный flex-контейнер** `flex h-screen overflow-hidden flex-col md:flex-row` — на `< md` Sidebar становится нижней навигацией (order-last), на `md+` сидит слева. `<main>` имеет fluid padding `p-3 sm:p-4 md:p-5 lg:p-6` и `pb-14 md:pb-5 lg:pb-6` (отступ под bottom-nav на мобилке). Внутри main — `<div className="max-w-full md:max-w-[1920px] mx-auto w-full">` (ограничение только с md, на узких — full-width). Без header. |
| `globals.css` | `@layer base :root {}` с CSS vars. Актуальные значения: `--card: 222 47% 16%`, `--border: 217 33% 22%`, `--muted: 217 33% 20%`, `--accent: 217 33% 25%`. **Fluid type scale** `--font-size-xs` … `--font-size-3xl` через `clamp()`, диапазон 360—2560 px (для 20:9—9:20 adaptation). **Responsive sidebar width var** `--sidebar-width`: `0px` (mobile, bottom-nav) → `3.5rem` с `md` (icon-only) → `14rem` с `lg` (expanded). `.status-dot-ok` (pulse-dot анимация). |
| `page.tsx` | Dashboard Bento Grid. В header рядом с title теперь есть компактный monospace badge с `connectionTarget`; ниже остаётся отдельный заметный `Card`: `Connected to` + `connectionTarget` (host/IP и режимный badge `localhost` / `admin-online`). Значение приходит из `/api/health` и в split deployment даёт оператору мгновенную проверку, что admin-head смотрит на правильный backend-host, а не в локальный `localhost`. В connection-card показываются `Kafka bootstrap` и broker error, если `KAFKA_BOOTSTRAP_SERVERS` недоступен. Row 1: `StatCard` (×4) с `border-l-4` акцентами (`grid-cols-2 xl:grid-cols-4`). Row 2: `grid-cols-1 lg:grid-cols-2` — стек из 6 `ServiceCard` слева: 2 Kafka health (`microservice_data`, `microservice_analitic`) и 4 HTTP probe через `fetchInfraHealth()` (`Redpanda`, `MinIO`, `account`, `gateway`), `CoverageBar` chart справа. Маппинг infra → `ServiceHealth`: `online` → `{ status: 'ok' }`, `offline` → `{ status: 'error', error }`. В online-head режиме те же карточки продолжают работать через env-переопределения `ONLINE_*`, без локальной `modelline_net`. `anyLoading` = `dataLoading` + `tablesLoading` + `modelsLoading` + `infraLoading` (OR). Row 3: Dataset shadcn Table. Авто-рефреш через `useEvents(EVT_ANALYTICS_MODEL_READY)`. Empty-state placeholders в `StatCard.value` и `<span>` ячейках coverage-таблицы — `'–'` (en-dash, U+2013); JSX-комментарии — обычный `-`. **SQLite-backed cache**: на маунте читает `modelline:dashboard:v1` (TTL 60 мин) → восстанавливает `tables/coverage/modelCount` до завершения `refresh()`; после загрузки таблиц — `cacheWrite` fire-and-forget. Health-состояния не кешируются. |

### Страницы

| Маршрут | Файл | Описание |
| ------- | ---- | -------- |
| `/download` | `download/page.tsx` | **Dataset страница.** Layout: `grid-cols-1 lg:grid-cols-[380px,1fr]`. Левая колонка: конфигурация, кнопки операций и ingest progress UI; правая: coverage/stat cards; ниже Available Tables, Quality Block и Action History. Ingest-button теперь стоит в одной строке с exchange selector (`bybit`, `binance`, `kraken`), а выбранные `symbol/timeframe/dateFrom/dateTo/exchange` зеркалятся из `localStorage` в server-side SQLite state (`modelline:params:dataset`), поэтому переживают restart admin-контейнера. `handleIngest` больше не делает долгий ingest RPC: single-TF и `ALL` сначала `await refreshCoverageState()`, затем отправляют быстрый `CMD_DATA_DATASET_JOBS_START` (`timeoutMs: 5_000`) и переводят TF в `queued`. `running` приходит из remote job state (`useDatasetJobs`): SSE остаётся primary-каналом, а для tracked ingest jobs добавлен fallback polling `cmd.data.dataset.jobs.list` + `cmd.data.dataset.jobs.get` раз в 5 с, чтобы UI не застревал в ложном `queued/stalled`, если split admin пропустил progress/completed event. Если remote job реально создана, `loadingIngest` и page-level lock удерживаются до её terminal-state; если старт не состоялся, локальный busy-state снимается сразу по явному отказу backend. Для `ALL` локальный `AllIngestProgress` строится вокруг `allIngestJobIds` + `DatasetJobView[]`: рендерит 4 execution slot-а, очередь queued jobs, stalled-banner если очередь не двигается, и recent done/error list; running-slot показывает stage/progress/detail/elapsed/short job id, а succeeded+`completed=0` читается как нормальный no-op. Детальный `DatasetJobsPanel` из Download убран и вынесен на `/queue`, чтобы dataset-операторский экран не смешивал общую live-очередь со scoped ingest-виджетом. Ошибки старта (`schema_not_ready`, `bad_request`, `unsupported_exchange`, `db_unavailable`, `pg_*`, `internal_error`) сразу переводят конкретный TF в `error` без ложного running. Backend пока реально исполняет ingest только для `bybit`; `binance` и `kraken` доступны в UI как подготовка к будущим коннекторам и сейчас честно отклоняются. Coverage не обнуляется; после terminal jobs, quality-check и repair выполняется `refreshCoverageState()` и `handleListTables()`, поэтому UI всегда опирается на последнее реальное состояние БД. Dataset export остаётся Kafka-only и zero-byte для Admin: `/api/export/csv` возвращает только `presigned_url` без host-нормализации и без legacy raw-localhost fallback'а — URL уже подписан data-сервисом на browser-facing origin (внешний вход infra-nginx, по умолчанию `http://localhost:8501`), а `/modelline-blobs/*` стримит файл напрямую из MinIO; страница `download/page.tsx` использует ссылку как есть (`a.href = presigned_url; a.click()`), без `explainExportDownloadPath`-проверок и без переписывания host. Остальные операции (`handleCheckCoverage`, `handleDeleteRows`, `handleRepairDataset`, quality/repair, fix-all) сохраняют Kafka-only ownership: admin только запускает команды и отображает удалённый прогресс/результат. |
| `/train` | `train/page.tsx` | Кастомный tab-switcher в `<header>`. Layout: `grid-cols-1 lg:grid-cols-2`. Левая: Config Card + Status Card (если обучается: `ProgressLine` при ≥2 точках, иначе `Progress`). Правая: Training History table. State: `progressHistory: StepPoint[]`, сбрасывается при `handleTrain`. Поллинг 3 с + `useEvents(EVT_ANALYTICS_TRAIN_PROGRESS)` для real-time обновлений прогресса. |
| `/compare` | `compare/page.tsx` | CSS grid 2 колонки. shadcn Card в каждой: Select (symbol/timeframe) + Button Load + shadcn Table predictions. Кнопка Export CSV |
| `/anomaly` | `anomaly/page.tsx` | **Anomaly Inspection Panel v2.** 8-блочная панель: расширенная детекция (Rolling Z/IQR, Stale, Return, Volume mismatch), Isolation Forest, Timeline scatter chart, Distribution diagnostics (skew/kurt/JB), inline params для clean operations, Smart Suggestions с одно-кнопочным Apply, History (audit log) и CSV/JSON Export. Долгосрочная панель инспекции, очистки и ML-аномалий. Header: Symbol + Timeframe Select + Button `Analyze` (+ `localStorage('modelline:params:anomaly')` + session badge: `{symbol} {timeframe} · {row_count} rows · {memory_mb_on_disk} MB on disk` + Unload button). `operationLockRef: useRef<boolean>(false)` гейтит конкурентные операции (Analyze/Apply/DBSCAN/Load) — race-free shared state. **`handleAnalyze`** — 4 параллельных `kafkaCall` через `Promise.all`: `CMD_DATA_DATASET_COLUMN_STATS`, `CMD_DATA_DATASET_COVERAGE`, `CMD_DATA_DATASET_DETECT_ANOMALIES` (`{ table, step_ms }`, timeout 120 c), `CMD_ANALITIC_DATASET_STATUS`. После — fire-and-forget `CMD_ANALITIC_DATASET_LOAD` если сессия не загружена для текущей пары (timeout 600 c). **Inspect** (default-open) — Summary Bar (Total Rows / Columns / Avg Null % / Date Range) + df.info()-style таблица (Column / Dtype / Non-Null / Null / Null % / Min / Max / Mean / Std) через `CMD_DATA_DATASET_COLUMN_STATS`. Null% > 5 — `warning`-бейдж, > 20 — `destructive`-бейдж. Клик по строке с численным dtype раскрывает lazy-fetched гистограмму (`CMD_DATA_DATASET_COLUMN_HISTOGRAM`, 30 buckets, dynamic import `HistogramChart`). **Browse** (collapsed) — постраничный просмотр строк (`CMD_DATA_DATASET_BROWSE`) с per-column time-series chart (`BrowseAreaChart`). **Anomalies** (default-open) — 3 summary-карточки (Critical/Warning/Total с цветовой индикацией), by-type chips, фильтры severity (all/critical/warning) + type, paginated table (50/page) с tinting по severity. **DBSCAN sub-block** (collapsed внутри Anomalies) — input-ы `eps=0.5`, `min_samples=5`, `max_sample_rows=50_000`, кнопка `Run DBSCAN` → `CMD_ANALITIC_ANOMALY_DBSCAN` (timeout 300 c, требует загруженной сессии); summary-карточки с n_clusters/n_anomalies/sample_size. **Clean** — checkbox-список 5 операций (drop_duplicates, fix_ohlc, fill_zero_streaks, delete_by_timestamps, fill_gaps) с counts из preview, выбор `interpolation_method` (forward_fill/linear) при `fill_gaps`. **Preview** → `CMD_DATA_DATASET_CLEAN_PREVIEW` (timeout 120 c). **Apply** (variant destructive) → confirm-диалог `"Это изменит данные в PostgreSQL. Продолжить?"` → `CMD_DATA_DATASET_CLEAN_APPLY` с `{ confirm: true, ...cleanOps, step_ms, interpolation_method }` (timeout 600 c) → success-toast `"Applied: {total} rows changed (audit #{audit_id})"` → локальная переменная `shouldReanalyze=true` → в блоке `finally` сначала `operationLockRef.current = false`, **затем** `void handleAnalyze()` (защита от race condition: предыдущая версия сбрасывала флаг до вызова rerun). **Session lifecycle**: one-shot `CMD_ANALITIC_DATASET_STATUS` на mount; `handleUnloadSession` → `CMD_ANALITIC_DATASET_UNLOAD`, очищает badge и DBSCAN-результат. **localStorage `modelline:params:anomaly`** хранит `{ symbol, timeframe, cleanOps, interpolationMethod, dbscanEps, dbscanMinSamples, dbscanMaxSampleRows }` — все галочки и параметры DBSCAN сохраняются между сессиями. **Все четыре секции** (Inspect, Browse, Anomalies, Clean) открыты по умолчанию (`defaultOpen`). **SQLite-backed cache**: при смене symbol/timeframe — `cacheRead` по ключу `modelline:anomaly:v1:{symbol}:{timeframe}` (TTL 30 мин) → восстанавливает `stats`/`coverage`/`anomalies`. После `handleAnalyze` — `cacheWrite` с тем же набором полей. DBSCAN и Clean preview не кешируются. |
| `/logs` | `logs/page.tsx` | **Operator Logs.** Шестой пункт левого меню. Client page читает `GET /api/logs?limit=250`, автообновляется каждые 5 с, показывает counters Total/Warnings/Errors и таблицу runtime events (`time`, `level`, `source`, `event`, JSON details). Кнопка `Run Check` запускает `/api/health`, `cmd.data.health` и `cmd.analytics.health`, чтобы сразу наполнить admin-side trace; `Clear` вызывает `DELETE /api/logs`. Источник — process-local буфер admin runtime, без token/payload. |
| `/queue` | `queue/page.tsx` | **Operator Queue.** Седьмой пункт левого меню. Страница подключает `useDatasetJobsFeed(1500)` и совмещает `DatasetJobsPanel` с отдельной **queue history** из `GET /api/queue/history?limit=250`, а не с общим runtime log stream. Верхние counters показывают active/running/queued/finished dataset jobs и число queue-request errors; ниже идут live dataset jobs и таблица только крупных операций (`jobs.start/cancel`, delete/clean/export, repair/recompute, train/anomaly run) с duration, payload/response summary, exchange и correlation id. История читается из SQLite-backed store через `/api/queue/history`, поэтому completed queue-операции переживают restart admin-контейнера. Ping/health и polling `jobs.list/get` намеренно исключены и остаются только на `/logs`. `Clear` очищает только queue history, но не вмешивается в фактические jobs владельца-сервиса и не трогает общий runtime log buffer. |

---

## src/components/

### Shared components

| Файл | Описание |
| ---- | --------- |
| `Sidebar.tsx` | **Трёхрежимная адаптивная навигация.** `detectMode()` по `window.innerWidth` + `resize` listener возвращает `'expanded-collapsible' \| 'icon-only' \| 'bottom-nav'`. **Mode A (≥ 1024 px)** — expanded-collapsible: `collapsed` state (`false`=`w-56`, `true`=`w-14`, `transition-all duration-200`), тоггл-кнопка `ChevronLeft/ChevronRight`, `localStorage('modelline:sidebar:collapsed')`. **Mode B (768—1023 px)** — icon-only: всегда `w-14`, тоггл скрыт (`{!isIconOnly && …}`), `effectiveCollapsed = true`. **Mode C (< 768 px)** — bottom-nav: early return `<aside className="order-last flex flex-row w-full h-14 border-t">`, `nav` = `flex-row items-stretch justify-around`, каждый Link = `flex flex-1 flex-col items-center justify-center gap-0.5`, показывается только иконка с `aria-label={label}`. Ключ `order-last` + parent `flex-col md:flex-row` дают nav внизу. Sidebar каждые 30 с читает `/api/health`: в expanded-mode `connectionTarget` показывается и под логотипом в brand header, и в footer рядом со статусом Kafka, чтобы target backend был виден глобально на любой странице. Навигация: Dashboard / Download / Train / Compare / Anomaly / **Logs** (`ScrollText`) / **Queue** (`ListOrdered`). |
| `Toast.tsx` | Глобальные toast-уведомления. Хук `useToast()` + `ToastProvider`. Типы: `success`, `error`, `info`. Авто-закрытие 4 с |

### src/components/ui/ (shadcn/ui компоненты)

| Файл | Примитив | Описание |
| ---- | -------- | -------- |
| `button.tsx` | — | CVA: variant(default/destructive/outline/secondary/ghost/link), size(default/sm/lg/icon). Radix Slot для `asChild` |
| `card.tsx` | — | Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter |
| `badge.tsx` | — | CVA: default/secondary/destructive/outline/success/warning/info |
| `skeleton.tsx` | — | `animate-pulse rounded-md bg-primary/10` |
| `progress.tsx` | `@radix-ui/react-progress` | Root + Indicator с translateX transition |
| `table.tsx` | — | Table, TableHeader, TableBody, TableRow, TableHead, TableCell |
| `tabs.tsx` | `@radix-ui/react-tabs` | Tabs, TabsList, TabsTrigger, TabsContent |
| `select.tsx` | `@radix-ui/react-select` | Select, SelectTrigger, SelectValue, SelectContent, SelectItem |
| `separator.tsx` | `@radix-ui/react-separator` | horizontal/vertical `bg-border` |
| `tooltip.tsx` | `@radix-ui/react-tooltip` | TooltipProvider, Tooltip, TooltipTrigger, TooltipContent |
| `input.tsx` | — | `flex h-9 w-full rounded-md border border-input bg-card` |
| `collapsible.tsx` | — | Локальный свёртываемый блок (useState + ChevronDown с `rotate-180`). Props: `title`, `defaultOpen?`, `open?`, `onOpenChange?`. Body монтируется только при open (ленивая загрузка вложенных запросов). |

---

## src/lib/ (утилиты)

| Файл | Ключевые объекты | Описание |
| ---- | ---------------- | -------- |
| `utils.ts` | `cn(...inputs)` | `clsx` + `tailwind-merge` утилита для shadcn |
| `sqliteStore.ts` | `readStoredValue()`, `writeStoredValue()`, `appendQueueHistoryRow()`, `readQueueHistoryRows()` | **Server-only** (`import 'server-only'`). Встроенный `node:sqlite` store для admin runtime state. Создаёт `kv_store` и `queue_history`, включает `WAL`, TTL-cleanup для `kv_store` и ограничивает queue history последними 400 строками. Путь берётся из `SQLITE_DB_PATH` или fallback `process.cwd()/.runtime-data/admin-state.sqlite`. |
| `cacheClient.ts` | `cacheRead<T>(key)`, `cacheWrite(key, value, ttl)` | **Browser-safe**. Обращается к `/api/cache` через `fetch`. Значения сериализуются в JSON. Все ошибки поглощаются. |
| `kafka.ts` | `kafkaRequest()`, `kafkaStatus()`, `probeKafkaConnectivity()` | **Server-only. Long-lived reply-inbox.** Singleton Kafka producer + consumer; reply-inbox `reply.microservice_admin.<instance>` создаётся ОДИН раз при первом вызове и живёт до завершения процесса. Запрос = `producer.send` + `await` ожидающего `Promise<…>` в `Map<correlation_id, …>`. Цикл консьюмера матчит входящие envelopes по `correlation_id` и резолвит вызывающего. Никаких per-request createTopics/sleep/deleteTopics — латентность падает с ~700 мс до < 50 мс, поток ephemeral-топиков иссяк (см. `microservice_infra/docker-compose.yml`). При `SIGTERM`/`SIGINT` все pending-запросы получают reject; consumer/producer disconnect. Workaround для KafkaJS+Redpanda v24 сохранён: reply inbox создаётся через admin перед subscribe c `waitForLeaders: true`, чтобы remote `admin-online` не ловил race `This server does not host this topic-partition` на только что созданном reply topic. |
| `backendClient.ts` | `backendCall()`, `BackendClientError`, `isSplitMode`, `ADMIN_BACKEND_BASE_URL`, `ADMIN_BACKEND_TLS_INSECURE` | **Server-only. HTTP-клиент для gateway admin facade (split deployment).** Когда `ADMIN_BACKEND_BASE_URL` задан, маршрутизирует все Kafka-топики в `POST /api/admin/<path>` на backend-хосте вместо прямого Kafka. Shared secret передаётся в `Authorization: Bearer`. Если `ADMIN_BACKEND_TLS_INSECURE=1` и backend URL использует `https://`, модуль выставляет `NODE_TLS_REJECT_UNAUTHORIZED=0`, чтобы Next runtime принимал autogenerated self-signed cert backend facade. Содержит полную таблицу `TOPIC_PATH` (44 топика). `BackendClientError` несёт HTTP-статус, `code`, `detail` и `correlationId`; отдельно классифицирует missing/invalid token, `admin_backend_tls_untrusted`, `502` upstream, `503` backend Kafka unavailability, `504` timeout и network failures с причиной из `err.cause`. Runtime diagnostics идут в container logs тегом `[admin-backend]` без печати shared token. |
| `sseHub.ts` | `subscribe(fn)`, `sseHubStatus()` | **Server-only. Один Kafka-consumer на процесс для всех `EVT_*` топиков.** Browser-вкладка → `/api/events` → `subscribe(callback)` добавляет callback в `Set<Subscriber>`, fan-out внутри `consumer.run`. Group `admin-sse` (стабильный, чтобы рестарт процесса не плодил новых групп). Ленивая инициализация при первом `/api/events`. Сравните с предыдущим дизайном (отдельный consumer + group + admin.createTopics на каждый таб) — теперь N открытых вкладок = 1 consumer-group вместо N. |
| `kafkaCoalesce.ts` | `coalesce(key, ttl, factory)`, `coalesceTtlFor()`, `makeKey()` | Server-only. Короткий TTL-кэш для read-only summary-запросов через `/api/kafka`. Aллоулист топиков: health (1.5 c), `list_tables`/`coverage`/`dataset.status` (2 c), `model.list` (5 c), `table_schema` (10 c), `constants` (30 c). Стабильный JSON-ключ payload'а. Mutating-команды (ingest, clean, train, anomaly run) проходят без коалесинга; коалесинг отключается, когда вызывающий передал собственный `correlationId`. |
| `kafkaClient.ts` | `kafkaCall<T>()`, `newCorrelationId()` | Client-side: `POST ${NEXT_PUBLIC_BASE_PATH}/api/kafka`, десериализует ответ. `kafkaCall(topic, payload, timeoutMsOrOptions)` — 3-й параметр совместим как с legacy `number` (timeoutMs), так и с `KafkaCallOptions = { timeoutMs?, correlationId? }`. `newCorrelationId()` — hex (crypto.randomUUID без дефисов) для предварительного генерирования id на клиенте (позволяет слушать события с тем же `correlation_id` до завершения команды). URL строится через `process.env.NEXT_PUBLIC_BASE_PATH ?? ''`. |
| `healthClient.ts` | `fetchInfraHealth()` | Client-side: `GET ${NEXT_PUBLIC_BASE_PATH}/api/health`, возвращает `InfraHealthResponse` (gateway/account/redpanda/minio + `connectionTarget` + реальный Kafka broker status/`bootstrapServers`). URL строится через `process.env.NEXT_PUBLIC_BASE_PATH ?? ''`. |
| `adminRuntimeLog.ts` | `writeAdminRuntimeLog()`, `readAdminRuntimeLogs()`, `clearAdminRuntimeLogs()` | **Server-only.** Process-local ring buffer последних admin runtime diagnostics. Пишется из `/api/health`, `/api/kafka` и `backendClient.ts` параллельно с console logs. Максимум 500 записей, последние читаются `/api/logs`; shared token и payload не сохраняются. |
| `queueHistoryStore.ts` | `appendQueueHistory()`, `readQueueHistory()`, `clearQueueHistory()` | **Server-only.** Отдельный queue-history store для страницы `/queue`. Хранит success/error записи только по allowlist queue-topic'ов, сохраняет `topic`, `durationMs`, `payloadSummary`, `responseSummary`, `code/detail`, `correlationId` и делегирует persistence в `sqliteStore.ts`, поэтому история переживает restart admin-контейнера, пока жив `SQLITE_DB_PATH`. |
| `useDatasetJobsFeed.ts` | `useDatasetJobsFeed()` | Client-only hook для live dataset jobs sync на страницах `/download` и `/queue`: подписывает страницу на `EVT_DATA_DATASET_JOB_PROGRESS` / `...COMPLETED`, делает hydrate `refreshActiveJobs()` и опциональный background polling active jobs. |
| `topics.ts` | `Topics`, `replyInbox()` | Константы топиков Kafka. Аномалийные: `CMD_DATA_DATASET_COLUMN_STATS`, `CMD_DATA_DATASET_COLUMN_HISTOGRAM`, `CMD_DATA_DATASET_BROWSE`, `CMD_DATA_DATASET_DETECT_ANOMALIES`, `CMD_DATA_DATASET_CLEAN_PREVIEW`, `CMD_DATA_DATASET_CLEAN_APPLY`, `CMD_DATA_DATASET_AUDIT_LOG`. Сессионные/ML/диагностика: `CMD_ANALITIC_DATASET_LOAD`, `CMD_ANALITIC_DATASET_UNLOAD`, `CMD_ANALITIC_DATASET_STATUS`, `CMD_ANALITIC_ANOMALY_DBSCAN`, `CMD_ANALITIC_ANOMALY_ISOLATION_FOREST`, `CMD_ANALITIC_DATASET_DISTRIBUTION`. |
| `exportFile.ts` | `rowsToCsv()`, `downloadCsv()`, `downloadJson()`, `buildReportFilename()` | Browser-side download helpers. CSV/JSON файлы создаются через `Blob` + `URL.createObjectURL` без backend round-trip. CSV получает UTF-8 BOM для корректного открытия в Excel. Используется в Anomaly → Export. |
| `types.ts` | `ServiceHealth`, `TableCoverage`, `TrainStatus`, `PredictionRow`, `CoverageDetail`, `ExportResult`, `TrainProgressEvent`, `ModelReadyEvent`, `InfraServiceHealth`, `InfraHealthResponse`, `IngestStage`, `RepairStageId` (`'prepare' \| 'fetch' \| 'upsert' \| 'recompute'`), `RepairStage`, `RepairProgressEvent`, `QualityStatus` (`'full' \| 'partial' \| 'missing'`), `QualityGroupReport`, `QualityReport` | TypeScript-типы |
| `constants.ts` | `SYMBOLS`, `TIMEFRAMES`, `TIMEFRAMES_ALL`, `TF_STEP_MS`, `makeTableName()` | Константы |

---

## src/hooks/

| Файл | Описание |
| ---- | -------- |
| `useHistory.ts` | `HistoryEntry` тип, localStorage (`modelline:history`), max 100 записей, `addEntry()` |
| `useEvents.ts` | SSE hook `useEvents(handlers)`. Открывает `EventSource('${NEXT_PUBLIC_BASE_PATH}/api/events')` на mount, диспатчит `{ type, payload }` в соответствующий handler, закрывает на unmount. Handlers в ref (без реконнекта). `EventHandlers` = `Partial<{ EVT_ANALYTICS_TRAIN_PROGRESS, EVT_ANALYTICS_MODEL_READY, EVT_DATA_INGEST_PROGRESS, EVT_ANALITIC_DATASET_REPAIR_PROGRESS }>`. |

## src/components/charts/ (Recharts компоненты)

| Файл | Описание |
| ---- | -------- |
| `CoverageBar.tsx` | `BarChart layout="vertical"` (горизонтальные бары). Props: `data: BarDatum[]` (`{ name, pct }`), `height?`. XAxis: 0–100%, YAxis: category (имя таблицы, truncated 20 chars). Primary fill color. Custom tooltip. Dynamic-import safe (только client). |
| `ProgressLine.tsx` | `LineChart` двух линий: `loss` (primary) и `val_loss` (warning, опционально). Props: `points: StepPoint[]` (`{ step, loss?, val_loss? }`), `height?`. Dot только на последней точке. `isAnimationActive={false}`. Dynamic-import safe. |
| `HistogramChart.tsx` | `BarChart` для гистограмм колонок. Props: `data: HistogramBucket[]` (`{ range_start, range_end, count }`), `height?`. Цвета HSL: MUTED_FG `hsl(215 20% 65%)`, PRIMARY `hsl(217 91% 60%)`, BG_CARD `hsl(222 47% 16%)`, BORDER `hsl(217 33% 22%)`. Dynamic import ssr:false. |
| `BrowseAreaChart.tsx` | `AreaChart` для временных рядов из Browse-секции. Props: `data: { ts: number; val: number }[]`. Те же inline HSL-цвета, градиент `browseGrad`, `isAnimationActive={false}`, custom tooltip (дата `toLocaleString` + `fmtNum`). Dynamic import ssr:false. |
| `AnomalyTimelineChart.tsx` | `ScatterChart` для Anomaly → Timeline tab. Props: `data: AnomalyTimelinePoint[]`, `types: string[]` (стабильный порядок строк). Категориальная Y-ось реализована через числовой YAxis + `tickFormatter` маппит индекс→имя типа (избегает багов recharts с `type='category'` на scatter). Цвет каждой точки через `<Cell>` (red=critical / yellow=warning). Custom tooltip с timestamp + severity + details. |
| `ReturnDistributionChart.tsx` | `ComposedChart` (Bar + Line) для Anomaly → Distribution tab. Props: `data: DistributionBin[]` (`{x, count, normal}`). Гистограмма log-доходностей + наложенная нормальная кривая, отскейленная под expected counts. Используется для визуального теста на heavy tails. |

---

## API Routes (server-side)

| Маршрут | Файл | Описание |
| ------- | ---- | -------- |
| `POST /api/kafka` | `api/kafka/route.ts` | Универсальный backend proxy. Body: `{ topic, payload?, timeoutMs? }`. **Local mode**: маршрутизирует в Kafka через `lib/kafka.ts`. **Split mode** (`ADMIN_BACKEND_BASE_URL`): вызывает `backendCall()` через `lib/backendClient.ts`. Read-only топики коалесцируются в обоих режимах. Возвращает `{ data }` или `{ error, status?, code?, detail?, correlationId? }`, чтобы UI видел конкретную причину split-facade отказа. Runtime diagnostics идут в container logs тегом `[api/kafka]`: branch, topic, timeout, coalesce, duration, code и correlationId. |
| `GET /api/logs` / `DELETE /api/logs` | `api/logs/route.ts` | Возвращает или очищает process-local admin runtime log buffer. `GET ?limit=N` ограничен диапазоном 1..500. Используется страницей `/logs`; `dynamic = 'force-dynamic'`. |
| `GET /api/queue/history` / `DELETE /api/queue/history` | `api/queue/history/route.ts` | Возвращает или очищает отдельную queue history. `GET ?limit=N` ограничен диапазоном 1..400. Источник не смешивается с `/api/logs`: Queue читает только этот route, а backing store — SQLite queue_history table, поэтому completed queue-операции сохраняются отдельно от общего admin runtime trace и переживают restart контейнера. |
| `GET /api/cache` | `api/cache/route.ts` | SQLite state bridge. GET `?key=X` → `{ value: string\|null }`. POST `{ key, value, ttl? }` → `{ ok: true }`. Сервер-единственный мост между браузером и `sqliteStore.ts`. TTL по умолчанию 3600 с. |
| `GET /api/health` | `api/health/route.ts` | Параллельный HTTP health-probe (через `Promise.allSettled`) для четырёх server-side адресов: `REDPANDA_ADMIN_URL/v1/status/ready`, `MINIO_URL/minio/health/live`, `ACCOUNT_URL/health`, `GATEWAY_URL/health/ready` с compatibility fallback на legacy `GATEWAY_URL/health`, если readiness endpoint ещё не опубликован и вернул `404`. Дополнительно route делает server-side Kafka connectivity probe через `lib/kafka.ts` и возвращает broker-level `kafka.status`, `bootstrapServers`, `replyInbox?`, `error?`. Таймаут HTTP probe — `2 000 мс` (`AbortSignal.timeout`). Local-stack использует docker-hostname, online-head — те же поля, но заполненные из `ONLINE_*` namespace на уровне compose (`admin-online`). В split mode route вычисляет `ADMIN_BACKEND_BASE_URL` из runtime env на каждый запрос, при `ADMIN_BACKEND_TLS_INSECURE=1` включает self-signed-friendly Node TLS mode и сначала проверяет `ADMIN_BACKEND_BASE_URL/health/ready`, а при `404` автоматически откатывается на `ADMIN_BACKEND_BASE_URL/health`. Route по-прежнему отдаёт полный `InfraHealthResponse` с валидным `KafkaBrokerHealth` (`online/offline`, `bootstrapServers`) и согласованными service-status полями для dashboard. Runtime diagnostics идут в container logs тегом `[api/health]`: выбранная ветка, backend URL, TLS flag, probe status/error и duration; fallback отдельно логируется как `probe:fallback`. В ответ дополнительно включается `connectionTarget` из runtime env `BACKEND_CONNECTION_TARGET`: local stack жёстко отдаёт `localhost`, `admin-online` — `ONLINE_BACKEND_HOST`, чтобы dashboard мог явно показать текущий backend-host. Sidebar использует именно этот broker-level статус, а не `cmd.data.db.ping`, поэтому `Kafka error` теперь значит реальную недоступность Kafka path, а не косвенный отказ data-service reply. `dynamic = 'force-dynamic'` |
| `GET /api/events` | `api/events/route.ts` | SSE стрим. Подписывается на все `EVT_*`-топики Kafka, передаёт `data: {"type", "payload"}\n\n`. Новый consumer с unique groupId на каждое подключение (`allowAutoTopicCreation: false`). Перед `consumer.connect()` явно создаёт все `EVT_*` через `admin.createTopics({ numPartitions: 1, replicationFactor: 1, waitForLeaders: false })` (тот же workaround что и в `lib/kafka.ts`: иначе KafkaJS падает с `INVALID_PARTITIONS` на `MetadataRequest v6` и стрим переподключается каждые несколько секунд). После `admin.disconnect()` — пауза `300 мс` для leader election. `TOPIC_ALREADY_EXISTS` игнорируется, прочее — `console.warn`. Cleanup при `request.signal.abort` и `cancel()`. `Content-Type: text/event-stream` |

---

## Kafka-топики которые использует admin

| Топик | Направление | Описание |
| ----- | ---------- | -------- |
| `cmd.data.health` | out | Health microservice_data |
| `cmd.data.db.ping` | out | Kafka healthcheck из Sidebar (таймаут 2 с, опрос каждые 30 с) |
| `cmd.data.dataset.list_tables` | out | Список таблиц |
| `cmd.data.dataset.coverage` | out | Диапазон дат / кол-во строк |
| `cmd.data.dataset.rows` | out | Срез данных |
| `cmd.data.dataset.ingest` | out | Запуск ingestion |
| `events.data.ingest.progress` | in | Поэтапный прогресс ingest-а для Download-страницы (SSE через `useEvents`) |
| `events.analitic.dataset.repair.progress` | in | Поэтапный прогресс audit-repair (`load_ohlcv` / `recompute_features`) для Quality-блока (SSE через `useEvents`, ключ `EVT_ANALITIC_DATASET_REPAIR_PROGRESS`) |
| `cmd.data.dataset.export` | out | Экспорт CSV, таймаут 300 с. Оба режима возвращают `{ presigned_url }`. Payload `{ table, start_ms, end_ms }` → DataService стримит CSV в MinIO, Admin передаёт URL как JSON 200. Payload `{ tables: string[], symbol: string, start_ms, end_ms }` (режим ALL) → DataService бундлирует ZIP через Pipe → MinIO, Admin передаёт URL как JSON 200. URL уже подписан data-сервисом на browser-facing origin (внешний вход infra-nginx, env `PUBLIC_DOWNLOAD_BASE_URL`, по умолчанию `http://localhost:8501`); `/modelline-blobs/*` стримит объект напрямую из MinIO. Admin **не нормализует** host и не имеет legacy fallback на raw `localhost:9000`/`minio:9000` — ответственность за корректный URL полностью на data-сервисе и infra-nginx. |
| `cmd.data.dataset.column_stats` | out | df.info()-style агрегаты (Non-Null / Min / Max / Mean / Std) по всем колонкам таблицы. Anomaly → Inspect. |
| `cmd.data.dataset.column_histogram` | out | Гистограмма распределения одной численной колонки (по умолчанию 30 buckets). Anomaly → Inspect (lazy-fetch по клику). |
| `cmd.data.dataset.detect_anomalies` | out | Детекция: gaps (`step_ms`), duplicates, OHLC violations, negatives, zero-streaks (`open_interest`/`funding_rate`), statistical outliers (`z=3.0`). Используется Anomaly → Anomalies. |
| `cmd.data.dataset.clean.preview` | out | Preview подсчёт строк, которые будут изменены каждой операцией (без мутации). Anomaly → Clean. |
| `cmd.data.dataset.clean.apply` | out | Применение очистки в БД (требует `confirm: true`, пишет `dataset_audit_log`). Anomaly → Clean. |
| `cmd.analitic.dataset.load` | out | Загрузка датасета в постоянную сессию AnalyticService (Parquet on disk). Anomaly → Analyze (background). |
| `cmd.analitic.dataset.unload` | out | Очистка сессии. Anomaly → Unload button. |
| `cmd.analitic.dataset.status` | out | Состояние сессии (badge). Anomaly → on mount + после Analyze. |
| `cmd.analitic.anomaly.dbscan` | out | Multivariate DBSCAN на загруженной сессии. Anomaly → DBSCAN tab. |
| `cmd.analitic.anomaly.isolation_forest` | out | Isolation Forest (sklearn) на загруженной сессии. Параметры: `contamination`, `n_estimators`, `max_sample_rows`. Anomaly → IForest tab. |
| `cmd.analitic.dataset.distribution` | out | Skewness, excess kurtosis, Jarque-Bera, гистограмма log-returns + N(μ,σ)-overlay. Anomaly → Distribution tab. |
| `cmd.data.dataset.audit_log` | out | Запросить последние записи `dataset_audit_log` (фильтр по `table`, лимит). Anomaly → History tab. |
| `cmd.analytics.train.start` | out | Запуск обучения |
| `cmd.analytics.train.status` | out | Статус обучения |
| `cmd.analytics.model.list` | out | Список моделей |
| `cmd.analytics.predict` | out | Прогноз |
| `events.analytics.train.progress` | in (SSE) | Прогресс обучения. Payload: `TrainProgressEvent` |
| `events.analytics.model.ready` | in (SSE) | Модель готова. Payload: `ModelReadyEvent` |
