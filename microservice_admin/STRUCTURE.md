# microservice_admin — Структура

> Обновляй этот файл при каждом изменении компонентов, страниц или библиотечных модулей.

---

## Dataset jobs (Phase G)

Длительные операции датасета теперь не блокируют UI:

- `src/hooks/useDatasetJobs.ts` — process-local store на
  `useSyncExternalStore`. `applyJobProgress` / `applyJobCompleted`
  мутируют `Map<job_id, DatasetJobView>`. `refreshActiveJobs()`
  однократно тянет активные jobs через `CMD_DATA_DATASET_JOBS_LIST`.
  `cancelJob(jobId)` отправляет `CMD_DATA_DATASET_JOBS_CANCEL`.
  Завершённые `succeeded/skipped` jobs авто-сворачиваются через 30 с;
  `failed/canceled` остаются до явного `dismissJob`.
- `src/components/DatasetJobsPanel.tsx` — компактный список с прогресс-
  барами, статусом, stage, error_code/error_message и кнопками
  «Отменить» / «Скрыть».
- `src/hooks/useEvents.ts` — добавлены кейсы
  `EVT_DATA_DATASET_JOB_PROGRESS` и `EVT_DATA_DATASET_JOB_COMPLETED`,
  пробрасываются в store.
- `src/app/download/page.tsx` — на mount вызывает
  `refreshActiveJobs()`; рендерит `<DatasetJobsPanel />` сверху.
  Межсервисное взаимодействие — только Kafka (HTTP только browser →
  Admin Next.js → Kafka).

---

## Корень сервиса

| Файл | Описание |
|------|-----------|
| `package.json` | Зависимости: `next@14`, `react@18`, `kafkajs@2`, `ioredis@5`, `uuid@10`, Tailwind CSS 3, shadcn/ui (Radix UI), lucide-react, class-variance-authority, clsx, tailwind-merge, tailwindcss-animate, **recharts ^2.15.0**. `@aws-sdk/client-s3` больше не используется — байты через Admin не проходят, DataService возвращает presigned URL для обоих режимов экспорта. |
| `next.config.js` | Next.js конфиг (App Router, environment proxy). `output: 'standalone'`, `basePath: '/admin'`, `assetPrefix: '/admin'`, `env.NEXT_PUBLIC_BASE_PATH: '/admin'`. basePath встраивается в билд — требует пересборки образа при изменении. |
| `tsconfig.json` | TypeScript-конфиг (`@/` → `src/`) |
| `tailwind.config.js` | Tailwind CSS конфиг: `darkMode: ['class']`, shadcn CSS var tokens, keyframes pulse-dot/shimmer/accordion. Кастомные экраны: `xs: '480px'` (phone landscape / small portrait), `3xl: '1920px'` (Full HD), `4xl: '2560px'` (4K) |
| `postcss.config.js` | PostCSS конфиг (CommonJS): регистрирует `tailwindcss` и `autoprefixer` как PostCSS плагины. **Критичен** — без него Next.js не обрабатывает директивы `@tailwind` в `globals.css` и utility-классы не генерируются (CSS bundle ~4 KB вместо ~26 KB). |
| `Dockerfile` | Multi-stage: `deps` → `builder` → `runner` (Node 20 Alpine) |
| `docker-compose.yml` | Сервис `admin` на порту 3000. Подключается к `modelline_net`. Env: `KAFKA_BOOTSTRAP_SERVERS`, `GATEWAY_URL`, `ACCOUNT_URL`, `REDPANDA_ADMIN_URL`, `REDIS_URL`. **MinIO**: `MINIO_URL` используется для `/api/health` liveness probe. `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` удалены — Admin больше не ходит в MinIO напрямую, DataService возвращает presigned URL. |

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
| `/download` | `download/page.tsx` | **Dataset страница.** Layout: `grid-cols-1 lg:grid-cols-[380px,1fr]`. Левая колонка (380px фикс): Dataset Configuration (Select/Input/кнопки) + `IngestProgress` под кнопками (6 стадий с заглушками pending/Loader2/CheckCircle2/XCircle, тонкая Progress-полоска в running). Селектор таймфрейма использует `TIMEFRAMES_ALL` (содержит `ALL` + все таймфреймы). **Режим ALL**: `handleIngest` разбивает `TIMEFRAMES` на батчи по `CONCURRENCY = 2` элемента и для каждого батча запускает оба `kafkaCall` одновременно через `Promise.allSettled`. Перед стартом батча оба TF переводятся в `'running'`, чтобы UI сразу показал их активными. Успех → `totalRows++`, `successes++`, статус `'done'`, обновление строки в `allCoverages`; ошибка одного TF → info-toast, статус `'error'`, второй TF батча не прерывается. Итоговый success-toast, `addEntry`, `handleListTables` вызываются один раз после завершения всех батчей. `handleCheckCoverage` в режиме ALL запрашивает `Promise.all` по всем `TIMEFRAMES` и сохраняет в `allCoverages: AllCoverageItem[]`; правая колонка отображает таблицу Timeframe/Rows/Coverage%/From/To. **`handleDeleteRows` (Очистить таблицу)**: при `timeframe === 'ALL'` — confirm-диалог с явным упоминанием всех таймфреймов, затем последовательный цикл по `TIMEFRAMES` с отдельным `CMD_DATA_DATASET_DELETE_ROWS` для каждого; ошибка отдельного TF → info-toast, цикл продолжается; финальный success-toast с суммарным количеством удалённых строк и числом успешных TF; при едином таймфрейме — поведение прежнее (один confirm + один вызов). В обоих ветках после успеха вызывается `handleListTables()`. Правая колонка: Coverage Card с `CoverageBar` (один бар, height=100) + 3 stat строки (Rows/Expected/Gaps) для единичного режима — появляется после Check Coverage. Ниже на всю ширину: Available Tables (строки не кликабельны, только отображение) → **Quality Block** (открывается кнопкой «Проверить целостность» в карточке конфигурации) → Action History. Dynamic import CoverageBar (ssr:false). **Ingest progress**: `handleIngest` генерирует `correlationId` через `newCorrelationId()` и передаёт его в `kafkaCall(..., { correlationId, timeoutMs: 60000 })`; `useEvents({ EVT_DATA_INGEST_PROGRESS })` фильтрует события по ref `ingestCidRef` (избегает stale closure), и на каждое событие обновляет соответствующую стадию (status + progress + detail). **Quality block**: открывается кнопкой «Проверить целостность» (ShieldCheck icon) в карточке конфигурации; строки таблицы Available Tables не кликабельны. **Исправить всё** (режим ALL): кнопка в заголовке Quality-блока, видна если хотя бы одна группа `status !== 'full'`; при выполнении заменяется кнопкой «Отменить» (устанавливает `fixAllCancelRef.current = true`). `handleFixAll` строит список операций из `allQualityResults`: для каждой таблицы — `load_ohlcv` первым (если нужен), `recompute_features` вторым (если нужен), по одному на тип; выполняет параллельно через планировщик с `CONCURRENCY = 4` — максимум 4 операции одновременно; для одной таблицы операции строго последовательны (`recompute_features` ждёт завершения `load_ohlcv` через per-table lock `runningTables: Set<string>`); операции разных таблиц могут выполняться параллельно без ограничений; `fixAllProgress: { current (= число завершённых операций), total, activeOps: { label: string }[], completed: { table, action, ok, errorMessage?: string }[], done, fixed, errors } | null`; прогресс-панель отображает до 4 активных задач одновременно, счётчик `current / total` отражает число уже завершённых, а не текущую позицию в очереди; для завершившихся с ошибкой операций в панели итогов отображается `errorMessage` (текст исключения, усечённый через `truncate` с `title`-атрибутом для полного текста в тултипе) как вторичная строка под меткой операции; после завершения показывает итог с возможностью закрыть панель. `runRepairSilent` — вспомогательная функция без per-repair UI-состояния (не трогает `repairStages/repairAction/loadingRepair`), вызывает Kafka-команду и re-audit. Индивидуальные кнопки ремонта заблокированы (`disabled`) пока выполняется `handleFixAll`. **Режим одного TF** (`timeframe !== 'ALL'`): `handleRepairDataset` вычисляет имя таблицы через `makeTableName`, устанавливает `selectedTable`, вызывает `runQualityCheck(table)` → `QualityReport` рендерится тремя строками-группами с цветной точкой (green=full / yellow=partial / red=missing) и `Progress`-баром по `fill_pct`. Для `status !== 'full'` — кнопка «Загрузить OHLCV» или «Пересчитать фичи» (по `g.repair_action`), которая вызывает `kafkaCall(CMD_ANALITIC_DATASET_LOAD_OHLCV / CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES, ..., { correlationId: cid, timeoutMs: 600_000 })`. **Режим ALL**: `handleRepairDataset` разбивает `TIMEFRAMES` на батчи по `CONCURRENCY = 2` и запускает каждые два `kafkaCall(CMD_ANALITIC_DATASET_QUALITY_CHECK, { table }, { timeoutMs: 60_000 })` одновременно через `Promise.allSettled`. Перед стартом батча оба TF переводятся в `'running'` в `qualityProgress.slots` с `startedAt: Date.now()`; после завершения — каждый слот получает статус `'done'` или `'error'` (с `message` при ошибке). Ошибочные слоты аккумулируются в `errorLog: { tf, message }[]`. Счётчик `done` инкрементируется только после завершения запроса. После финального батча выдерживается пауза 900 мс, затем `setLoadingQuality(false)`. Если `totalErrors > 0` — `qualityProgress.finished` становится `true` (блок остаётся видимым, тоаст не выдаётся); иначе — `setQualityProgress(null)`. Тип `qualityProgress: { done, total, slots: { tf, status: 'running'|'done'|'error', message?: string, startedAt?: number }[], errors: number, finished: boolean, errorLog: { tf, message }[] } | null`. `formatErrorHint(msg)` — чистая функция (вне компонента) для сокращения сообщений ошибок: timeout → «Таймаут ответа», table not found → «Таблица не найдена», column_stats failed → подстрока после двоеточия (макс 45 симв.), иначе — первые 45 симв. `useEffect` для таймера: запускает `setInterval(1000)` пока есть слоты в `'running'`, вызывая форс-ререндер через `setQualityProgress(prev => ({ ...prev }))`. Блок прогресса в карточке конфигурации: в режиме выполнения — заголовок (spinner/XCircle/CheckCircle2 + счётчик), Progress-полоска (красная если `errors > 0`), два слота с таймером («Ns» справа от имени TF при `running`) и строкой `formatErrorHint` под слотом при `error`; в режиме `finished` — кнопка «×» для закрытия вместо слотов, под прогресс-баром — секция «Детали ошибок:» с compact-списком `{tf}  •  {formatErrorHint(message)}` в `font-mono text-[10px]`, `max-h-24 overflow-y-auto`. В режиме ALL каждый entry списка показывает: имя таблицы, цветные точки-индикаторы по группам, строку-count; для таблиц с неполными группами — кнопки ремонта `«{group.label}: {действие}»`. `repairCidRef` фильтрует `EVT_ANALITIC_DATASET_REPAIR_PROGRESS`, обновляя `repairStages`; в режиме ALL прогресс-блок стадий рендерится inline под строкой активно-ремонтируемой таблицы (`selectedTable === table && repairStages`). `runQualityCheck` возвращает `QualityReport | null`; после repair результат через `setAllQualityResults(prev => ({ ...prev, [table]: fresh }))` обновляет конкретную строку в режиме ALL. Состояние `isAllMode: boolean` — дискриминатор режима отображения Quality-блока. **Export CSV — async со стриминговым прогресс-баром**: `handleExportCsv` async. States: `loadingExport: boolean` (входит в `isBusy`), `exportProgress: number | null` (0–100 = проценты, null = не активен). `handleExportCsv` вызывает `fetch(url)`, читает `Content-Length` из заголовков, затем читает `response.body` через `reader.read()` в цикле, накапливает чанки и после каждого обновляет `exportProgress = Math.round((received / total) * 100)`. По завершении собирает `Blob`, создаёт `URL.createObjectURL(blob)`, клик на невидимый `<a download>`, затем `URL.revokeObjectURL`. URL для `timeframe !== 'ALL'`: `${NEXT_PUBLIC_BASE_PATH}/api/export/csv?table=${makeTableName(symbol,tf)}&start_ms=&end_ms=` → сервер возвращает CSV с `Content-Length`; для `timeframe === 'ALL'`: `?symbol=&timeframe=ALL&start_ms=&end_ms=` → сервер возвращает ZIP с `Content-Length`. Под кнопкой Export CSV: `exportProgress !== null` → рендер `<Progress value={exportProgress} />` + строка `"{exportProgress}% — {filename}"`. Кнопка при `loadingExport=true`: spinner `<Loader2 animate-spin>`, disabled. Серверный `src/app/api/export/csv/route.ts` (runtime `'nodejs'`): вынесен хелпер `fetchMinioObject(claim: { key, bucket }): Promise<Uint8Array>` — создаёт `S3Client`, отправляет `GetObjectCommand`, вызывает `Body.transformToByteArray()`; используется в обеих ветках. ALL-ветка: `kafkaRequest({ tables, start_ms, end_ms }, { timeoutMs: 300_000 })` → `{ claim_check }` → `fetchMinioObject` → `Response(bytes)` с `Content-Type: application/zip`. Single-ветка: `kafkaRequest({ table, start_ms, end_ms }, { timeoutMs: 300_000 })` → `{ claim_check }` → `fetchMinioObject` → `Response(bytes)` с `Content-Type: text/csv` и `Content-Length`. **Redis cache**: при маунте читает `modelline:dataset-tables:v1` (TTL 60 мин) → восстанавливает `tables`. При смене symbol/timeframe — читает `modelline:dataset-coverage:v1:{symbol}:{timeframe}` и `modelline:dataset-allcoverage:v1:{symbol}` (TTL 30 мин) → восстанавливает `coverage` и `allCoverages`. После `handleListTables` и `handleCheckCoverage` — `cacheWrite` fire-and-forget. |
| `/train` | `train/page.tsx` | Кастомный tab-switcher в `<header>`. Layout: `grid-cols-1 lg:grid-cols-2`. Левая: Config Card + Status Card (если обучается: `ProgressLine` при ≥2 точках, иначе `Progress`). Правая: Training History table. State: `progressHistory: StepPoint[]`, сбрасывается при `handleTrain`. Поллинг 3 с + `useEvents(EVT_ANALYTICS_TRAIN_PROGRESS)` для real-time обновлений прогресса. |
| `/compare` | `compare/page.tsx` | CSS grid 2 колонки. shadcn Card в каждой: Select (symbol/timeframe) + Button Load + shadcn Table predictions. Кнопка Export CSV |
| `/anomaly` | `anomaly/page.tsx` | **Anomaly Inspection Panel v2.** 8-блочная панель: расширенная детекция (Rolling Z/IQR, Stale, Return, Volume mismatch), Isolation Forest, Timeline scatter chart, Distribution diagnostics (skew/kurt/JB), inline params для clean operations, Smart Suggestions с одно-кнопочным Apply, History (audit log) и CSV/JSON Export. Долгосрочная панель инспекции, очистки и ML-аномалий. Header: Symbol + Timeframe Select + Button `Analyze` (+ `localStorage('modelline:params:anomaly')` + session badge: `{symbol} {timeframe} · {row_count} rows · {memory_mb_on_disk} MB on disk` + Unload button). `operationLockRef: useRef<boolean>(false)` гейтит конкурентные операции (Analyze/Apply/DBSCAN/Load) — race-free shared state. **`handleAnalyze`** — 4 параллельных `kafkaCall` через `Promise.all`: `CMD_DATA_DATASET_COLUMN_STATS`, `CMD_DATA_DATASET_COVERAGE`, `CMD_DATA_DATASET_DETECT_ANOMALIES` (`{ table, step_ms }`, timeout 120 c), `CMD_ANALITIC_DATASET_STATUS`. После — fire-and-forget `CMD_ANALITIC_DATASET_LOAD` если сессия не загружена для текущей пары (timeout 600 c). **Inspect** (default-open) — Summary Bar (Total Rows / Columns / Avg Null % / Date Range) + df.info()-style таблица (Column / Dtype / Non-Null / Null / Null % / Min / Max / Mean / Std) через `CMD_DATA_DATASET_COLUMN_STATS`. Null% > 5 — `warning`-бейдж, > 20 — `destructive`-бейдж. Клик по строке с численным dtype раскрывает lazy-fetched гистограмму (`CMD_DATA_DATASET_COLUMN_HISTOGRAM`, 30 buckets, dynamic import `HistogramChart`). **Browse** (collapsed) — постраничный просмотр строк (`CMD_DATA_DATASET_BROWSE`) с per-column time-series chart (`BrowseAreaChart`). **Anomalies** (default-open) — 3 summary-карточки (Critical/Warning/Total с цветовой индикацией), by-type chips, фильтры severity (all/critical/warning) + type, paginated table (50/page) с tinting по severity. **DBSCAN sub-block** (collapsed внутри Anomalies) — input-ы `eps=0.5`, `min_samples=5`, `max_sample_rows=50_000`, кнопка `Run DBSCAN` → `CMD_ANALITIC_ANOMALY_DBSCAN` (timeout 300 c, требует загруженной сессии); summary-карточки с n_clusters/n_anomalies/sample_size. **Clean** — checkbox-список 5 операций (drop_duplicates, fix_ohlc, fill_zero_streaks, delete_by_timestamps, fill_gaps) с counts из preview, выбор `interpolation_method` (forward_fill/linear) при `fill_gaps`. **Preview** → `CMD_DATA_DATASET_CLEAN_PREVIEW` (timeout 120 c). **Apply** (variant destructive) → confirm-диалог `"Это изменит данные в PostgreSQL. Продолжить?"` → `CMD_DATA_DATASET_CLEAN_APPLY` с `{ confirm: true, ...cleanOps, step_ms, interpolation_method }` (timeout 600 c) → success-toast `"Applied: {total} rows changed (audit #{audit_id})"` → локальная переменная `shouldReanalyze=true` → в блоке `finally` сначала `operationLockRef.current = false`, **затем** `void handleAnalyze()` (защита от race condition: предыдущая версия сбрасывала флаг до вызова rerun). **Session lifecycle**: one-shot `CMD_ANALITIC_DATASET_STATUS` на mount; `handleUnloadSession` → `CMD_ANALITIC_DATASET_UNLOAD`, очищает badge и DBSCAN-результат. **localStorage `modelline:params:anomaly`** хранит `{ symbol, timeframe, cleanOps, interpolationMethod, dbscanEps, dbscanMinSamples, dbscanMaxSampleRows }` — все галочки и параметры DBSCAN сохраняются между сессиями. **Все четыре секции** (Inspect, Browse, Anomalies, Clean) открыты по умолчанию (`defaultOpen`). **Redis cache**: при смене symbol/timeframe — `cacheRead` по ключу `modelline:anomaly:v1:{symbol}:{timeframe}` (TTL 30 мин) → восстанавливает `stats`/`coverage`/`anomalies`. После `handleAnalyze` — `cacheWrite` с тем же набором полей. DBSCAN и Clean preview не кешируются. |

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
| `kafka.ts` | `kafkaRequest()`, `kafkaStatus()` | **Server-only. Long-lived reply-inbox.** Singleton Kafka producer + consumer; reply-inbox `reply.microservice_admin.<instance>` создаётся ОДИН раз при первом вызове и живёт до завершения процесса. Запрос = `producer.send` + `await` ожидающего `Promise<…>` в `Map<correlation_id, …>`. Цикл консьюмера матчит входящие envelopes по `correlation_id` и резолвит вызывающего. Никаких per-request createTopics/sleep/deleteTopics — латентность падает с ~700 мс до < 50 мс, поток ephemeral-топиков иссяк (см. `microservice_infra/docker-compose.yml`). При `SIGTERM`/`SIGINT` все pending-запросы получают reject; consumer/producer disconnect. Workaround для KafkaJS+Redpanda v24 (создание топика через admin перед subscribe + `allowAutoTopicCreation: false`) сохранён, но выполняется один раз. |
| `sseHub.ts` | `subscribe(fn)`, `sseHubStatus()` | **Server-only. Один Kafka-consumer на процесс для всех `EVT_*` топиков.** Browser-вкладка → `/api/events` → `subscribe(callback)` добавляет callback в `Set<Subscriber>`, fan-out внутри `consumer.run`. Group `admin-sse` (стабильный, чтобы рестарт процесса не плодил новых групп). Ленивая инициализация при первом `/api/events`. Сравните с предыдущим дизайном (отдельный consumer + group + admin.createTopics на каждый таб) — теперь N открытых вкладок = 1 consumer-group вместо N. |
| `kafkaCoalesce.ts` | `coalesce(key, ttl, factory)`, `coalesceTtlFor()`, `makeKey()` | Server-only. Короткий TTL-кэш для read-only summary-запросов через `/api/kafka`. Aллоулист топиков: health (1.5 c), `list_tables`/`coverage`/`dataset.status` (2 c), `model.list` (5 c), `table_schema` (10 c), `constants` (30 c). Стабильный JSON-ключ payload'а. Mutating-команды (ingest, clean, train, anomaly run) проходят без коалесинга; коалесинг отключается, когда вызывающий передал собственный `correlationId`. |
| `kafkaClient.ts` | `kafkaCall<T>()`, `newCorrelationId()` | Client-side: `POST ${NEXT_PUBLIC_BASE_PATH}/api/kafka`, десериализует ответ. `kafkaCall(topic, payload, timeoutMsOrOptions)` — 3-й параметр совместим как с legacy `number` (timeoutMs), так и с `KafkaCallOptions = { timeoutMs?, correlationId? }`. `newCorrelationId()` — hex (crypto.randomUUID без дефисов) для предварительного генерирования id на клиенте (позволяет слушать события с тем же `correlation_id` до завершения команды). URL строится через `process.env.NEXT_PUBLIC_BASE_PATH ?? ''`. |
| `healthClient.ts` | `fetchInfraHealth()` | Client-side: `GET ${NEXT_PUBLIC_BASE_PATH}/api/health`, возвращает `InfraHealthResponse` (gateway/account/redpanda/minio). URL строится через `process.env.NEXT_PUBLIC_BASE_PATH ?? ''`. |
| `topics.ts` | `Topics`, `replyInbox()` | Константы топиков Kafka. Аномалийные: `CMD_DATA_DATASET_COLUMN_STATS`, `CMD_DATA_DATASET_COLUMN_HISTOGRAM`, `CMD_DATA_DATASET_BROWSE`, `CMD_DATA_DATASET_DETECT_ANOMALIES`, `CMD_DATA_DATASET_CLEAN_PREVIEW`, `CMD_DATA_DATASET_CLEAN_APPLY`, `CMD_DATA_DATASET_AUDIT_LOG`. Сессионные/ML/диагностика: `CMD_ANALITIC_DATASET_LOAD`, `CMD_ANALITIC_DATASET_UNLOAD`, `CMD_ANALITIC_DATASET_STATUS`, `CMD_ANALITIC_ANOMALY_DBSCAN`, `CMD_ANALITIC_ANOMALY_ISOLATION_FOREST`, `CMD_ANALITIC_DATASET_DISTRIBUTION`. |
| `exportFile.ts` | `rowsToCsv()`, `downloadCsv()`, `downloadJson()`, `buildReportFilename()` | Browser-side download helpers. CSV/JSON файлы создаются через `Blob` + `URL.createObjectURL` без backend round-trip. CSV получает UTF-8 BOM для корректного открытия в Excel. Используется в Anomaly → Export. |
| `types.ts` | `ServiceHealth`, `TableCoverage`, `TrainStatus`, `PredictionRow`, `CoverageDetail`, `ExportResult`, `TrainProgressEvent`, `ModelReadyEvent`, `InfraServiceHealth`, `InfraHealthResponse`, `IngestStage`, `RepairStageId` (`'prepare' \| 'fetch' \| 'upsert' \| 'recompute'`), `RepairStage`, `RepairProgressEvent`, `QualityStatus` (`'full' \| 'partial' \| 'missing'`), `QualityGroupReport`, `QualityReport` | TypeScript-типы |
| `constants.ts` | `SYMBOLS`, `TIMEFRAMES`, `TIMEFRAMES_ALL`, `TF_STEP_MS`, `makeTableName()` | Константы |

---

## src/hooks/

| Файл | Описание |
|------|-----------|
| `useHistory.ts` | `HistoryEntry` тип, localStorage (`modelline:history`), max 100 записей, `addEntry()` || `useEvents.ts` | SSE hook `useEvents(handlers)`. Открывает `EventSource('${NEXT_PUBLIC_BASE_PATH}/api/events')` на mount, диспатчит `{ type, payload }` в соответствующий handler, закрывает на unmount. Handlers в ref (без реконнекта). `EventHandlers` = `Partial<{ EVT_ANALYTICS_TRAIN_PROGRESS, EVT_ANALYTICS_MODEL_READY, EVT_DATA_INGEST_PROGRESS, EVT_ANALITIC_DATASET_REPAIR_PROGRESS }>`. URL строится через `process.env.NEXT_PUBLIC_BASE_PATH ?? ''`. |---

## src/components/charts/ (Recharts компоненты)

| Файл | Описание |
|------|----------|
| `CoverageBar.tsx` | `BarChart layout="vertical"` (горизонтальные бары). Props: `data: BarDatum[]` (`{ name, pct }`), `height?`. XAxis: 0–100%, YAxis: category (имя таблицы, truncated 20 chars). Primary fill color. Custom tooltip. Dynamic-import safe (только client). |
| `ProgressLine.tsx` | `LineChart` двух линий: `loss` (primary) и `val_loss` (warning, опционально). Props: `points: StepPoint[]` (`{ step, loss?, val_loss? }`), `height?`. Dot только на последней точке. `isAnimationActive={false}`. Dynamic-import safe.
| `HistogramChart.tsx` | `BarChart` для гистограмм колонок. Props: `data: HistogramBucket[]` (`{ range_start, range_end, count }`), `height?`. Цвета HSL: MUTED_FG `hsl(215 20% 65%)`, PRIMARY `hsl(217 91% 60%)`, BG_CARD `hsl(222 47% 16%)`, BORDER `hsl(217 33% 22%)`. Dynamic import ssr:false. |
| `BrowseAreaChart.tsx` | `AreaChart` для временных рядов из Browse-секции. Props: `data: { ts: number; val: number }[]`. Те же inline HSL-цвета, градиент `browseGrad`, `isAnimationActive={false}`, custom tooltip (дата `toLocaleString` + `fmtNum`). Dynamic import ssr:false. |
| `AnomalyTimelineChart.tsx` | `ScatterChart` для Anomaly → Timeline tab. Props: `data: AnomalyTimelinePoint[]`, `types: string[]` (стабильный порядок строк). Категориальная Y-ось реализована через числовой YAxis + `tickFormatter` маппит индекс→имя типа (избегает багов recharts с `type='category'` на scatter). Цвет каждой точки через `<Cell>` (red=critical / yellow=warning). Custom tooltip с timestamp + severity + details. |
| `ReturnDistributionChart.tsx` | `ComposedChart` (Bar + Line) для Anomaly → Distribution tab. Props: `data: DistributionBin[]` (`{x, count, normal}`). Гистограмма log-доходностей + наложенная нормальная кривая, отскейленная под expected counts. Используется для визуального теста на heavy tails. |

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
| `events.analitic.dataset.repair.progress` | in | Поэтапный прогресс audit-repair (`load_ohlcv` / `recompute_features`) для Quality-блока (SSE через `useEvents`, ключ `EVT_ANALITIC_DATASET_REPAIR_PROGRESS`) |
| `cmd.data.dataset.export` | out | Экспорт CSV, таймаут 300 с. Оба режима возвращают `{ presigned_url }`. Payload `{ table, start_ms, end_ms }` → DataService стримит CSV в MinIO, Admin передаёт URL как JSON 200. Payload `{ tables: string[], symbol: string, start_ms, end_ms }` (режим ALL) → DataService бундлирует ZIP через Pipe → MinIO, Admin передаёт URL как JSON 200. Браузер скачивает напрямую с MinIO. |
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