# microservice_admin — Структура

> Обновляй этот файл при каждом изменении компонентов, страниц или библиотечных модулей.

---

## Корень сервиса

| Файл | Описание |
|------|-----------|
| `package.json` | Зависимости: `next@14`, `react@18`, `kafkajs@2`, `ioredis@5`, `uuid@10`, `@aws-sdk/client-s3@^3.600.0`, Tailwind CSS 3, shadcn/ui (Radix UI), lucide-react, class-variance-authority, clsx, tailwind-merge, tailwindcss-animate, **recharts ^2.15.0**. `@aws-sdk/client-s3` используется **только** в ZIP-ветке `/api/export/csv` (экспорт всех таймфреймов одним файлом через claim-check). Single-table экспорт не ходит в MinIO напрямую — браузер получает 302 на presigned URL от DataService. |
| `next.config.js` | Next.js конфиг (App Router, environment proxy). `output: 'standalone'`, `basePath: '/admin'`, `assetPrefix: '/admin'`, `env.NEXT_PUBLIC_BASE_PATH: '/admin'`. basePath встраивается в билд — требует пересборки образа при изменении. |
| `tsconfig.json` | TypeScript-конфиг (`@/` → `src/`) |
| `tailwind.config.js` | Tailwind CSS конфиг: `darkMode: ['class']`, shadcn CSS var tokens, keyframes pulse-dot/shimmer/accordion. Кастомные экраны: `xs: '480px'` (phone landscape / small portrait), `3xl: '1920px'` (Full HD), `4xl: '2560px'` (4K) |
| `postcss.config.js` | PostCSS конфиг (CommonJS): регистрирует `tailwindcss` и `autoprefixer` как PostCSS плагины. **Критичен** — без него Next.js не обрабатывает директивы `@tailwind` в `globals.css` и utility-классы не генерируются (CSS bundle ~4 KB вместо ~26 KB). |
| `Dockerfile` | Multi-stage: `deps` → `builder` → `runner` (Node 20 Alpine) |
| `docker-compose.yml` | Сервис `admin` на порту 3000. Подключается к `modelline_net`. Env: `KAFKA_BOOTSTRAP_SERVERS`, `GATEWAY_URL`, `ACCOUNT_URL`, `REDPANDA_ADMIN_URL`, `REDIS_URL`. **MinIO**: `MINIO_URL` используется и для `/api/health` (liveness probe), и для ZIP-ветки `/api/export/csv` (S3 GetObject). `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` — только для ZIP-ветки (single-table экспорт ходит через presigned URL от DataService и не требует этих переменных). |

---

## src/app/ (Next.js App Router)

### Корень

| Файл | Описание |
|------|-----------|
| `layout.tsx` | Root layout: Inter шрифт, **адаптивный flex-контейнер** `flex h-screen overflow-hidden flex-col md:flex-row` — на `< md` Sidebar становится нижней навигацией (order-last), на `md+` сидит слева. `<main>` имеет fluid padding `p-3 sm:p-4 md:p-5 lg:p-6` и `pb-14 md:pb-5 lg:pb-6` (отступ под bottom-nav на мобилке). Внутри main — `<div className="max-w-full md:max-w-[1920px] mx-auto w-full">` (ограничение только с md, на узких — full-width). Без header. |
| `globals.css` | `@layer base :root {}` с CSS vars. Актуальные значения: `--card: 222 47% 16%`, `--border: 217 33% 22%`, `--muted: 217 33% 20%`, `--accent: 217 33% 25%`. **Fluid type scale** `--font-size-xs` … `--font-size-3xl` через `clamp()`, диапазон 360—2560 px (для 20:9—9:20 adaptation). **Responsive sidebar width var** `--sidebar-width`: `0px` (mobile, bottom-nav) → `3.5rem` с `md` (icon-only) → `14rem` с `lg` (expanded). `.status-dot-ok` (pulse-dot анимация). |
| `page.tsx` | Dashboard Bento Grid. Row 1: `StatCard` (×4) с `border-l-4` акцентами (`grid-cols-2 xl:grid-cols-4`). Row 2: `grid-cols-1 lg:grid-cols-2` — стек из 6 `ServiceCard` слева (1 Kafka: `microservice_data`; 5 HTTP через `fetchInfraHealth()`: `microservice_analitic`, `microservice_gateway`, `microservice_account`, `Redpanda`, `MinIO`), `CoverageBar` chart справа. `microservice_analitic` — HTTP-only FastAPI без Kafka-потребителя, поэтому health берётся из `/api/health`, не из Kafka request-reply (Kafka-запрос `CMD_ANALYTICS_HEALTH` висел бы в таймауте). Маппинг infra → `ServiceHealth`: `online` → `{ status: 'ok' }`, `offline` → `{ status: 'error', error }`. `anyLoading` = `dataLoading` + `tablesLoading` + `modelsLoading` + `infraLoading` (OR). Row 3: Dataset shadcn Table. Авто-рефреш через `useEvents(EVT_ANALYTICS_MODEL_READY)`. Empty-state placeholders в `StatCard.value` и `<span>` ячейках coverage-таблицы — `'–'` (en-dash, U+2013); JSX-комментарии — обычный `-`. **Redis cache**: на маунте читает `modelline:dashboard:v1` (TTL 60 мин) → восстанавливает `tables/coverage/modelCount` до завершения `refresh()`; после загрузки таблиц — `cacheWrite` fire-and-forget. Health-состояния не кешируются. |

### Страницы

| Маршрут | Файл | Описание |
|---------|------|----------|
| `/download` | `download/page.tsx` | **Dataset страница.** Layout: `grid-cols-1 lg:grid-cols-[380px,1fr]`. Левая колонка (380px фикс): Dataset Configuration (Select/Input/кнопки) + `IngestProgress` под кнопками (6 стадий с заглушками pending/Loader2/CheckCircle2/XCircle, тонкая Progress-полоска в running). Селектор таймфрейма использует `TIMEFRAMES_ALL` (содержит `ALL` + все таймфреймы). **Режим ALL**: `handleIngest` разбивает `TIMEFRAMES` на батчи по `CONCURRENCY = 2` элемента и для каждого батча запускает оба `kafkaCall` одновременно через `Promise.allSettled`. Перед стартом батча оба TF переводятся в `'running'`, чтобы UI сразу показал их активными. Успех → `totalRows++`, `successes++`, статус `'done'`, обновление строки в `allCoverages`; ошибка одного TF → info-toast, статус `'error'`, второй TF батча не прерывается. Итоговый success-toast, `addEntry`, `handleListTables` вызываются один раз после завершения всех батчей. `handleCheckCoverage` в режиме ALL запрашивает `Promise.all` по всем `TIMEFRAMES` и сохраняет в `allCoverages: AllCoverageItem[]`; правая колонка отображает таблицу Timeframe/Rows/Coverage%/From/To. **`handleDeleteRows` (Очистить таблицу)**: при `timeframe === 'ALL'` — confirm-диалог с явным упоминанием всех таймфреймов, затем последовательный цикл по `TIMEFRAMES` с отдельным `CMD_DATA_DATASET_DELETE_ROWS` для каждого; ошибка отдельного TF → info-toast, цикл продолжается; финальный success-toast с суммарным количеством удалённых строк и числом успешных TF; при едином таймфрейме — поведение прежнее (один confirm + один вызов). В обоих ветках после успеха вызывается `handleListTables()`. Правая колонка: Coverage Card с `CoverageBar` (один бар, height=100) + 3 stat строки (Rows/Expected/Gaps) для единичного режима — появляется после Check Coverage. Ниже на всю ширину: Available Tables + Action History. Dynamic import CoverageBar (ssr:false). **Ingest progress**: `handleIngest` генерирует `correlationId` через `newCorrelationId()` и передаёт его в `kafkaCall(..., { correlationId, timeoutMs: 60000 })`; `useEvents({ EVT_DATA_INGEST_PROGRESS })` фильтрует события по ref `ingestCidRef` (избегает stale closure), и на каждое событие обновляет соответствующую стадию (status + progress + detail). **Export CSV — single-click**: `handleExportCsv` синхронная (без `await`), создаёт один `<a>` и вызывает `.click()` внутри пользовательского жеста. Для `timeframe !== 'ALL'`: URL `${NEXT_PUBLIC_BASE_PATH}/api/export/csv?table=${makeTableName(symbol,tf)}&start_ms=&end_ms=`, `a.download="${table}.csv"` — сервер отвечает 302 на MinIO presigned URL (DataService стримит `COPY TO STDOUT` → MinIO multipart → 60-мин presigned URL, `response-content-disposition` зашит в подпись). Для `timeframe === 'ALL'`: URL `?symbol=&timeframe=ALL&start_ms=&end_ms=`, `a.download="${symbol}_ALL.zip"` — один файл вместо 11. Серверный `src/app/api/export/csv/route.ts` (runtime `'nodejs'`): в ALL-ветке разворачивает массив таблиц из импортированного `TIMEFRAMES`, шлёт `kafkaRequest({ tables, start_ms, end_ms }, { timeoutMs: 300_000 })`, получает `{ claim_check: { key, bucket } }`, тянет объект через `@aws-sdk/client-s3` `GetObjectCommand` (`endpoint: http://${MINIO_URL}`, `forcePathStyle: true`, creds из `MINIO_ACCESS_KEY/SECRET_KEY`), возвращает байты с `Content-Type: application/zip` и `Content-Disposition: attachment; filename="${symbol}_ALL.zip"`. В single-ветке: `kafkaRequest({ table, start_ms, end_ms }, { timeoutMs: 300_000 })` → `{ presigned_url }` → `NextResponse.redirect(url, 302)`. Старый цикл `for (const tf of TIMEFRAMES) a.click(); await sleep(300)` удалён — Chromium блокировал программные загрузки после первых нескольких. **Redis cache**: при маунте читает `modelline:dataset-tables:v1` (TTL 60 мин) → восстанавливает `tables`. При смене symbol/timeframe — читает `modelline:dataset-coverage:v1:{symbol}:{timeframe}` и `modelline:dataset-allcoverage:v1:{symbol}` (TTL 30 мин) → восстанавливает `coverage` и `allCoverages`. После `handleListTables` и `handleCheckCoverage` — `cacheWrite` fire-and-forget. |
| `/train` | `train/page.tsx` | Кастомный tab-switcher в `<header>`. Layout: `grid-cols-1 lg:grid-cols-2`. Левая: Config Card + Status Card (если обучается: `ProgressLine` при ≥2 точках, иначе `Progress`). Правая: Training History table. State: `progressHistory: StepPoint[]`, сбрасывается при `handleTrain`. Поллинг 3 с + `useEvents(EVT_ANALYTICS_TRAIN_PROGRESS)` для real-time обновлений прогресса. |
| `/compare` | `compare/page.tsx` | CSS grid 2 колонки. shadcn Card в каждой: Select (symbol/timeframe) + Button Load + shadcn Table predictions. Кнопка Export CSV |
| `/anomaly` | `anomaly/page.tsx` | **Anomaly Inspection Panel.** Долгосрочная панель инспекции, очистки и обработки датасетов. Header: Symbol + Timeframe Select + Button `Analyze` (+ `localStorage('modelline:params:anomaly')` для persistence). 4 локальных свёртываемых секции (`Collapsible`): **Inspect** (default-open) — Summary Bar (Total Rows / Columns / Avg Null % / Date Range через `CMD_DATA_DATASET_COVERAGE`) + df.info()-style таблица (Column / Dtype / Non-Null / Null / Null % / Min / Max / Mean / Std) через `CMD_DATA_DATASET_COLUMN_STATS`. Null% > 5 — `warning`-бейдж, > 20 — `destructive`-бейдж. Клик по строке с численным dtype (`numeric/double precision/real/integer/bigint/smallint`) раскрывает строку с lazy-fetched гистограммой (`CMD_DATA_DATASET_COLUMN_HISTOGRAM`, 30 buckets) через `HistogramChart` (dynamic import, ssr:false). Повторный клик сворачивает, гистограмма кэшируется по имени колонки. **Anomalies / Clean / Process** — placeholder-карточки «Coming soon» с описанием следующих этапов (детекция аномалий, preview-cleanup, производные признаки). **Redis cache**: при смене symbol/timeframe — `cacheRead` по ключу `modelline:anomaly:v1:{symbol}:{timeframe}` (TTL 30 мин) → восстанавливает `stats` и `coverage`. После успешного `handleAnalyze` — `cacheWrite` fire-and-forget. Гистограммы и Browse-строки не кешируются. |

---

## src/components/

### Shared components

| Файл | Описание |
|------|-----------|
| `Sidebar.tsx` | **Трёхрежимная адаптивная навигация.** `detectMode()` по `window.innerWidth` + `resize` listener возвращает `'expanded-collapsible' \| 'icon-only' \| 'bottom-nav'`. **Mode A (≥ 1024 px)** — expanded-collapsible: `collapsed` state (`false`=`w-56`, `true`=`w-14`, `transition-all duration-200`), тоггл-кнопка `ChevronLeft/ChevronRight`, `localStorage('modelline:sidebar:collapsed')`. **Mode B (768—1023 px)** — icon-only: всегда `w-14`, тоггл скрыт (`{!isIconOnly && …}`), `effectiveCollapsed = true`. **Mode C (< 768 px)** — bottom-nav: early return `<aside className="order-last flex flex-row w-full h-14 border-t">`, `nav` = `flex-row items-stretch justify-around`, каждый Link = `flex flex-1 flex-col items-center justify-center gap-0.5`, показывается только иконка с `aria-label={label}`. Ключ `order-last` + parent `flex-col md:flex-row` дают nav внизу. Kafka healthcheck каждые 30 с во всех режимах. Footer/dot/версия видны только в Mode A (expanded). Навигация: Dashboard / Download / Train / Compare / **Anomaly** (`ShieldAlert`). |
| `Toast.tsx` | Глобальные toast-уведомления. Хук `useToast()` + `ToastProvider`. Типы: `success`, `error`, `info`. Авто-закрытие 4 с |

### src/components/ui/ (shadcn/ui компоненты)

| Файл | Примитив | Описание |
|------|----------|----------|
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
|------|-----------------|----------|
| `utils.ts` | `cn(...inputs)` | `clsx` + `tailwind-merge` утилита для shadcn |
| `redisCache.ts` | `cacheGet(key)`, `cacheSet(key, value, ttlSeconds)` | **Server-only** (`import 'server-only'`). Singleton `ioredis` клиент (URL из `REDIS_URL`). Параметры: `lazyConnect, enableOfflineQueue:false, connectTimeout:2000, commandTimeout:1000, maxRetriesPerRequest:0, retryStrategy:()=>null`. Все ошибки поглощаются — Redis недоступен = прозрачный fallback. |
| `cacheClient.ts` | `cacheRead<T>(key)`, `cacheWrite(key, value, ttl)` | **Browser-safe**. Обращается к `/api/cache` через `fetch`. Значения сериализуются в JSON. Все ошибки поглощаются. |
| `kafka.ts` | `kafkaRequest()` | Server-only. Singleton Kafka producer + consumer. Request-reply паттерн. Timeout 15 с. **Workaround для KafkaJS 2.x + Redpanda:** consumer создаётся с `allowAutoTopicCreation: false`, reply-inbox топик явно создаётся через `admin.createTopics({ numPartitions: 1, replicationFactor: 1, waitForLeaders: false })` перед `consumer.connect()`. `waitForLeaders: false` обязателен — Redpanda v24 возвращает неконсистентный ответ при `true`, и KafkaJS бросает исключение, хотя топик реально создаётся. `TOPIC_ALREADY_EXISTS` (код 36) тихо игнорируется, прочие ошибки логируются через `console.warn` без проброса. После `admin.disconnect()` — пауза `500 мс` для leader election в single-node Redpanda (иначе `NOT_LEADER_OR_FOLLOWER` на `consumer.connect()`) |
| `kafkaClient.ts` | `kafkaCall<T>()`, `newCorrelationId()` | Client-side: `POST ${NEXT_PUBLIC_BASE_PATH}/api/kafka`, десериализует ответ. `kafkaCall(topic, payload, timeoutMsOrOptions)` — 3-й параметр совместим как с legacy `number` (timeoutMs), так и с `KafkaCallOptions = { timeoutMs?, correlationId? }`. `newCorrelationId()` — hex (crypto.randomUUID без дефисов) для предварительного генерирования id на клиенте (позволяет слушать события с тем же `correlation_id` до завершения команды). URL строится через `process.env.NEXT_PUBLIC_BASE_PATH ?? ''`. |
| `healthClient.ts` | `fetchInfraHealth()` | Client-side: `GET ${NEXT_PUBLIC_BASE_PATH}/api/health`, возвращает `InfraHealthResponse` (gateway/account/redpanda/minio). URL строится через `process.env.NEXT_PUBLIC_BASE_PATH ?? ''`. |
| `topics.ts` | `Topics`, `replyInbox()` | Константы топиков Kafka. Аномалийные: `CMD_DATA_DATASET_COLUMN_STATS`, `CMD_DATA_DATASET_COLUMN_HISTOGRAM`, `CMD_DATA_DATASET_BROWSE`, `CMD_DATA_DATASET_DETECT_ANOMALIES`, `CMD_DATA_DATASET_CLEAN`. |
| `types.ts` | `ServiceHealth`, `TableCoverage`, `TrainStatus`, `PredictionRow`, `CoverageDetail`, `ExportResult`, `TrainProgressEvent`, `ModelReadyEvent`, `InfraServiceHealth`, `InfraHealthResponse` | TypeScript-типы |
| `constants.ts` | `SYMBOLS`, `TIMEFRAMES`, `TIMEFRAMES_ALL`, `TF_STEP_MS`, `makeTableName()` | Константы |

---

## src/hooks/

| Файл | Описание |
|------|-----------|
| `useHistory.ts` | `HistoryEntry` тип, localStorage (`modelline:history`), max 100 записей, `addEntry()` || `useEvents.ts` | SSE hook `useEvents(handlers)`. Открывает `EventSource('${NEXT_PUBLIC_BASE_PATH}/api/events')` на mount, диспатчит `{ type, payload }` в соответствующий handler, закрывает на unmount. Handlers в ref (без реконнекта). `EventHandlers` = `Partial<{ EVT_ANALYTICS_TRAIN_PROGRESS, EVT_ANALYTICS_MODEL_READY, EVT_DATA_INGEST_PROGRESS }>`. URL строится через `process.env.NEXT_PUBLIC_BASE_PATH ?? ''`. |---

## src/components/charts/ (Recharts компоненты)

| Файл | Описание |
|------|----------|
| `CoverageBar.tsx` | `BarChart layout="vertical"` (горизонтальные бары). Props: `data: BarDatum[]` (`{ name, pct }`), `height?`. XAxis: 0–100%, YAxis: category (имя таблицы, truncated 20 chars). Primary fill color. Custom tooltip. Dynamic-import safe (только client). |
| `ProgressLine.tsx` | `LineChart` двух линий: `loss` (primary) и `val_loss` (warning, опционально). Props: `points: StepPoint[]` (`{ step, loss?, val_loss? }`), `height?`. Dot только на последней точке. `isAnimationActive={false}`. Dynamic-import safe.
| `HistogramChart.tsx` | `BarChart` для гистограмм колонок. Props: `data: HistogramBucket[]` (`{ range_start, range_end, count }`), `height?`. Цвета HSL: MUTED_FG `hsl(215 20% 65%)`, PRIMARY `hsl(217 91% 60%)`, BG_CARD `hsl(222 47% 16%)`, BORDER `hsl(217 33% 22%)`. Dynamic import ssr:false. |
| `BrowseAreaChart.tsx` | `AreaChart` для временных рядов из Browse-секции. Props: `data: { ts: number; val: number }[]`. Те же inline HSL-цвета, градиент `browseGrad`, `isAnimationActive={false}`, custom tooltip (дата `toLocaleString` + `fmtNum`). Dynamic import ssr:false. |
---

## API Routes (server-side)

| Маршрут | Файл | Описание |
|---------|------|----------|
| `POST /api/kafka` | `api/kafka/route.ts` | Универсальный Kafka proxy. Body: `{ topic, payload?, timeoutMs? }`. Возвращает `{ data }` или `{ error }` |
| `GET /api/cache` | `api/cache/route.ts` | Redis cache bridge. GET `?key=X` → `{ value: string\|null }`. POST `{ key, value, ttl? }` → `{ ok: true }`. Сервер-единственный мост между браузером и Redis (через `redisCache.ts`). TTL по умолчанию 3600 с. |
| `GET /api/health` | `api/health/route.ts` | Параллельный HTTP health-probe (через `Promise.allSettled`) для пяти сервисов: `GATEWAY_URL/health`, `ACCOUNT_URL/health`, `REDPANDA_ADMIN_URL/v1/status/ready`, `MINIO_URL/minio/health/live`, `ANALITIC_URL/health`. Таймаут `2 000 мс` (`AbortSignal.timeout`). Response 2xx → `{ status: 'online' }`, иначе/исключение → `{ status: 'offline', error }`. `dynamic = 'force-dynamic'` |
| `GET /api/events` | `api/events/route.ts` | SSE стрим. Подписывается на все `EVT_*`-топики Kafka, передаёт `data: {"type", "payload"}\n\n`. Новый consumer с unique groupId на каждое подключение (`allowAutoTopicCreation: false`). Перед `consumer.connect()` явно создаёт все `EVT_*` через `admin.createTopics({ numPartitions: 1, replicationFactor: 1, waitForLeaders: false })` (тот же workaround что и в `lib/kafka.ts`: иначе KafkaJS падает с `INVALID_PARTITIONS` на `MetadataRequest v6` и стрим переподключается каждые несколько секунд). После `admin.disconnect()` — пауза `300 мс` для leader election. `TOPIC_ALREADY_EXISTS` игнорируется, прочее — `console.warn`. Cleanup при `request.signal.abort` и `cancel()`. `Content-Type: text/event-stream` |

---

## Kafka-топики которые использует admin

| Топик | Направление | Описание |
|-------|------------|----------|
| `cmd.data.health` | out | Health microservice_data |
| `cmd.data.db.ping` | out | Kafka healthcheck из Sidebar (таймаут 2 с, опрос каждые 30 с) |
| `cmd.data.dataset.list_tables` | out | Список таблиц |
| `cmd.data.dataset.coverage` | out | Диапазон дат / кол-во строк |
| `cmd.data.dataset.rows` | out | Срез данных |
| `cmd.data.dataset.ingest` | out | Запуск ingestion |
| `events.data.ingest.progress` | in  | Поэтапный прогресс ingest-а для Download-страницы (SSE через `useEvents`) |
| `cmd.data.dataset.export` | out | Экспорт CSV, таймаут 300 с. Payload `{ table, start_ms, end_ms }` → `{ presigned_url }` → Admin отвечает 302 redirect (байты не идут через Admin). Payload `{ tables: string[], start_ms, end_ms }` (режим ALL) → `{ claim_check }` → Admin тянет ZIP из MinIO через S3 GetObject и отдаёт браузеру с `Content-Disposition: attachment; filename="${symbol}_ALL.zip"`. |
| `cmd.data.dataset.column_stats` | out | df.info()-style агрегаты (Non-Null / Min / Max / Mean / Std) по всем колонкам таблицы. Anomaly → Inspect. |
| `cmd.data.dataset.column_histogram` | out | Гистограмма распределения одной численной колонки (по умолчанию 30 buckets). Anomaly → Inspect (lazy-fetch по клику). |
| `cmd.data.dataset.detect_anomalies` | out | **(Stage 2, not implemented yet)** Детекция выбросов / временных разрывов / нулевых серий. |
| `cmd.data.dataset.clean` | out | **(Stage 3, not implemented yet)** Preview + применение очистки (интерполяция, удаление выбросов, обрезка диапазона). |
| `cmd.analytics.train.start` | out | Запуск обучения |
| `cmd.analytics.train.status` | out | Статус обучения |
| `cmd.analytics.model.list` | out | Список моделей |
| `cmd.analytics.predict` | out | Прогноз |
| `events.analytics.train.progress` | in (SSE) | Прогресс обучения. Payload: `TrainProgressEvent` |
| `events.analytics.model.ready` | in (SSE) | Модель готова. Payload: `ModelReadyEvent` |