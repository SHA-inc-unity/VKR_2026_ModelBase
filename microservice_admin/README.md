# microservice_admin

**Роль:** Admin UI платформы ModelLine. Next.js 14 (App Router). Коммуницирует с `microservice_data` и `microservice_analitic` **исключительно через Kafka** (Redpanda). Никакого прямого HTTP между application-сервисами.

**Стек:** Next.js 14, React 18, TypeScript 5, Tailwind CSS 3, shadcn/ui, Radix UI, kafkajs, ioredis, recharts  
**Конфиги:** `tailwind.config.js` + `postcss.config.js` (последний критичен — регистрирует `tailwindcss` и `autoprefixer` как PostCSS плагины, без него `@tailwind` директивы не обрабатываются)  
**Порт:** `3000`  
**Base path:** `/admin` — приложение обслуживается по пути `/admin` (настроено через `basePath` и `assetPrefix` в `next.config.js`). Nginx пробрасывает `sha-trade.tech/admin` → `admin:3000`. Статика `_next/static/*` тоже проксируется корректно. Next.js **не** применяет `basePath` автоматически к `fetch()` и `EventSource` — все клиентские обращения к API используют `process.env.NEXT_PUBLIC_BASE_PATH ?? ''` как префикс (`healthClient.ts`, `kafkaClient.ts`, `useEvents.ts`).
**Зависимости:** `microservice_infra` (Redpanda broker, Redis)

## Redis Cache Layer

Страницы кешируют результаты дорогих Kafka-запросов в Redis, чтобы UI мгновенно
отображал предыдущие данные при перезагрузке страницы, не дожидаясь нового запроса.

### Компоненты

| Файл | Описание |
|------|----------|
| `src/lib/redisCache.ts` | **Server-only.** Singleton `ioredis` клиент (URL из `REDIS_URL`). Быстрые fast-fail параметры: `connectTimeout:2000`, `commandTimeout:1000`, `retryStrategy:()=>null`. Если Redis недоступен — ошибки поглощаются, fallback прозрачен. |
| `src/lib/cacheClient.ts` | **Browser-safe.** Не импортирует ioredis. `cacheRead<T>(key)` и `cacheWrite(key, value, ttl)` общаются с `/api/cache` через `fetch`. |
| `src/app/api/cache/route.ts` | Route Handler. GET `?key=X` → `{value}`. POST `{key,value,ttl?}` → `{ok:true}`. |

### Переменная окружения

| Переменная | Описание |
|-----------|----------|
| `REDIS_URL` | URL подключения к Redis. Пример: `redis://redis:6379`. Опциональна — отсутствие/недоступность Redis не вызывает ошибок. |

### Ключи и TTL

| Страница | Ключ | TTL | Что кешируется |
|----------|------|-----|----------------|
| Dashboard | `modelline:dashboard:v1` | 60 мин | `tables`, `coverage`, `modelCount` |
| Anomaly | `modelline:anomaly:v1:{symbol}:{timeframe}` | 30 мин | `stats`, `coverage` |
| Download | `modelline:dataset-tables:v1` | 60 мин | список таблиц (`DataTableInfo[]`) |
| Download | `modelline:dataset-coverage:v1:{symbol}:{timeframe}` | 30 мин | coverage для одного TF |
| Download | `modelline:dataset-allcoverage:v1:{symbol}` | 30 мин | coverage по всем TF (`AllCoverageItem[]`) |

### Паттерн

1. На маунте / смене параметров — `cacheRead` (если есть → мгновенно отображаем).
2. Параллельно / следом — Kafka-запрос за свежими данными.
3. После Kafka-ответа — `cacheWrite` fire-and-forget.

Здоровье сервисов (health-чеки), гистограммы и browse-строки **не** кешируются.

## Архитектура запросов

```
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
          redpanda admin | minio     ← только shared-infra, НЕ application
```

Клиентский код использует `kafkaCall()` из `src/lib/kafkaClient.ts` для команд (POST /api/kafka).
Для event-driven обновлений используется `useEvents()` хук, который открывает `EventSource('/api/events')`.
Health application-сервисов (`data`, `analitic`) — через Kafka (`cmd.*.health`), не через HTTP.
`fetchInfraHealth()` из `src/lib/healthClient.ts` остался только для инфраструктурных
health-чеков (Redpanda admin, MinIO) — эти два не слушают Kafka и находятся в
`modelline_net`, поэтому достижимы по имени контейнера. URL'ы задаются env-переменными
`REDPANDA_ADMIN_URL` / `MINIO_URL`. Прямого доступа браузера к Kafka нет.

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
|--------|------|----------|
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
|-------|-----|
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
|------|----------|
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
|-------------|-------------|--------------|----------|
| `events.analytics.model.ready` | `EVT_ANALYTICS_MODEL_READY` | Dashboard | Перезапрашивает список моделей (`modelCount`) |
| `events.analytics.train.progress` | `EVT_ANALYTICS_TRAIN_PROGRESS` | Train page | Добавляет точку в `progressHistory`, обновляет `status.progress` |

**Компоненты:**
- `GET /api/events` — SSE Route Handler. Подписывается на process-wide SSE-хаб (`src/lib/sseHub.ts`); каждое новое соединение лишь добавляет subscriber-callback в `Set<Subscriber>`. Один Kafka-consumer на процесс, fan-out всем подключённым клиентам. Heartbeat `:keepalive` каждые 25 с.
- `src/hooks/useEvents.ts` — React hook `useEvents(handlers)`. Открывает `EventSource` на mount, диспатчит payload в нужный handler, закрывает на unmount. Handlers хранятся в ref — не вызывает реконнект при перерендере.

## Компоненты UI

- **`src/components/Sidebar.tsx`** — сворачиваемая боковая навигация. Два состояния: `w-56` (развёрнут) / `w-14` (свёрнут), анимировано через `transition-all duration-200`. Кнопка ChevronLeft/Right для переключения; состояние сохраняется в `localStorage` (`modelline:sidebar:collapsed`). Логотип с lucide-react `Zap`. При развёрнутом sidebar — пульсирующий dot Kafka-статуса. Nav-иконки показывают `title` tooltip при свёрнутом состоянии. Kafka healthcheck на mount, повторяется каждые 30 с.
- **`src/components/Toast.tsx`** — глобальные toast-уведомления. Экспортирует хук `useToast()` и провайдер `ToastProvider`. Типы: `success`, `error`, `info`. Автозакрытие через 4 с.

## Страницы

| Страница | Описание |
|----------|----------|
| `/` (Dashboard) | Bento Grid: Row 1 — 4 StatCard с `border-l-4 border-l-{color}` акцентами (`grid-cols-2 xl:grid-cols-4`). Row 2 — 2 колонки: стек из 4 ServiceCard (2 application через Kafka: `data`, `analitic`; 2 infra через HTTP `/api/health`: `Redpanda`, `MinIO`) + `CoverageBar` (recharts BarChart horizontal). Row 3 — shadcn Table датасетов с `pct.toFixed(1)%`. Кнопка Refresh обновляет все карточки одновременно. |
| `/download` | 2-колоночный layout (`lg:grid-cols-[380px,1fr]`): левая — Dataset Configuration (Select/Input/кнопки + **Проверить целостность** кнопка); правая — Coverage Card с `CoverageBar` (один бар) + 3 stat строки, появляется после Check Coverage. Ниже: Available Tables (строки не кликабельны) + **Quality Block** (открывается кнопкой «Проверить целостность»; в режиме ALL при наличии хотя бы одной сломанной группы — кнопка **«Исправить всё»** запускает `handleFixAll`: последовательный ремонт всех сломанных групп по всем таймфреймам без параллелизма, порядок — `load_ohlcv` раньше `recompute_features`, дедупликация по типу на таблицу; прогресс показывается в панели `fixAllProgress`; кнопка «Отменить» появляется вместо «Исправить всё» во время выполнения; после завершения — итог с количеством исправленных и ошибок) + Action History на всю ширину. Таймфрейм `ALL` поддерживается в `handleIngest` (**concurrent batches**, concurrency = 2: батчи по 2 TF, каждый батч `Promise.allSettled`, один TF ошибка не прерывает второй), `handleCheckCoverage` (parallel), `handleDeleteRows` (sequential loop с confirm-диалогом). Все 5 async-handlers защищены `operationLockRef` (`useRef(false)`) — guard в начале, сброс в `finally` (при ранних `return` — перед return). **Repair Dataset**: `handleRepairDataset` — если `timeframe !== 'ALL'`, вычисляет `makeTableName`, вызывает `runQualityCheck(table)`, открывает Quality Block в режиме одного TF. Если `timeframe === 'ALL'`: запускает `kafkaCall(CMD_ANALITIC_DATASET_QUALITY_CHECK, { table }, { timeoutMs: 60_000 })` батчами по `CONCURRENCY = 2` через `Promise.allSettled`; перед каждым батчем оба TF переводятся в `'running'` в `qualityProgress.slots` с `startedAt: Date.now()`, после завершения — `'done'` или `'error'` (с `message`); ошибочные слоты накапливаются в `errorLog`. Счётчик `done` растёт после завершения запроса, не до. Тип: `qualityProgress: { done, total, slots: { tf, status, message?, startedAt? }[], errors: number, finished: boolean, errorLog: { tf, message }[] }`. После финального батча — пауза 900 мс при 100%, затем скрытие блока (если ошибок нет) или установка `finished: true` (если ошибки есть — блок остаётся, тоаст не выдаётся). `formatErrorHint(msg)` — чистая функция для сокращения ошибок (timeout/table not found/column_stats/обрезка 45 симв.). `useEffect`-таймер форс-ренедрит раз в секунду пока есть `'running'`-слоты. Прогресс-блок: при выполнении — spinner + счётчик + Progress (красный при ошибках) + два слота с таймером «Ns» и строкой `formatErrorHint` под ошибочным слотом; при `finished` — кнопка «×», секция «Детали ошибок:» с `max-h-24 overflow-y-auto` вместо слотов. В режиме ALL Quality Block показывает список таблиц с цветными точками-индикаторами по группам + кнопки ремонта; прогресс repair-стадий рендерится inline под строкой активно-ремонтируемой таблицы. После repair `runQualityCheck` обновляет конкретную запись в `allQualityResults`. **Export CSV**: `handleExportCsv` — async, с lock guard. State: `loadingExport` (входит в `isBusy`). Вызывает `fetch(url)`, ждёт JSON `{ presigned_url }` (Admin держит соединение открытым пока Kafka + DataService + MinIO завершают работу; байты через Admin не проходят). По получении URL создаёт невидимый `<a href={presigned_url} download>` → `click()` — браузер скачивает напрямую с MinIO. Прогресс-бар удалён; нативный прогресс отображается браузером в панели загрузок. Кнопка при `loadingExport`: spinner + `"Подготовка данных..."`, disabled. URL для `timeframe !== 'ALL'`: `?table=&start_ms=&end_ms=`; для `ALL`: `?symbol=&timeframe=ALL&start_ms=&end_ms=`. Серверный `route.ts` (runtime `'nodejs'`): без S3 SDK, оба режима вызывают `kafkaRequest` и возвращают `Response.json({ presigned_url })` 200. `handleDeleteRows` при `ALL`: confirm упоминает все таймфреймы, удаляет последовательно через `CMD_DATA_DATASET_DELETE_ROWS`, ошибка отдельного TF → info-toast, итог — success-toast. **Redis cache**: список таблиц, coverage и allCoverages восстанавливаются из Redis при маунте / смене symbol+timeframe (ключи см. таблицу выше). |
| `/train` | Кастомный tab-switcher в header (без Radix Tabs). 2-колоночный grid на `lg+`: левая — Config + Status Card с `ProgressLine` (recharts LineChart, показывается после ≥2 точек прогресса); правая — Training History table. `progressHistory` state сбрасывается при каждом новом запуске. |
| `/compare` | CSS grid 2 колонки, shadcn Card в каждой, shadcn Select, Button экспорта |
| `/anomaly` | Инспекция / ML-детекция / очистка / экспорт датасетов. **Detection parameters**: 4 inline-секции c чекбоксами и параметрами для новых типов — Rolling Z-score/IQR (window/threshold/mode), Frozen/stale price (min consecutive), Return outlier (threshold %), Volume/turnover mismatch (tolerance %). **Inspect**: df.info-таблица + lazy-гистограммы. **Browse**: постраничный просмотр сырых строк + per-column charts. **Anomalies секция** (новый layout): summary-карточки + Smart Suggestions panel (ранжированный по severity список рекомендаций с inline кнопкой "Apply" — мгновенно включает соответствующий clean checkbox и запускает Apply) + Tabs: **Timeline** (scatter chart с категориальной Y-осью, цвет точек = severity), **Table** (paginated detail с фильтрами), **DBSCAN** (eps/min_samples/max_sample_rows), **IForest** (Isolation Forest: contamination/n_estimators/max_sample_rows через `cmd.analitic.anomaly.isolation_forest`), **Distribution** (skewness, excess kurtosis, JB p-value + histogram log-returns с N(μ,σ)-overlay через `cmd.analitic.dataset.distribution`), **History** (lazy-load `cmd.data.dataset.audit_log` — все clean.apply записи; кнопка Rollback зарезервирована, disabled). **Clean**: 5 операций в карточках с inline-параметрами при checked — drop_duplicates имеет strategy (first/last/none), fill_zero_streaks — columns selector (all/volume/open_interest/funding_rate), fill_gaps — method (forward_fill/linear/drop_rows). **Export**: dropdown в header с выбором формата (CSV/JSON) и subset (all/critical/dbscan/iforest); скачивание через Blob+`URL.createObjectURL` без backend, файл `anomaly_report_{symbol}_{tf}_{ts}.{ext}`. **Session badge** (Analyze авто-загружает сессию через `cmd.analitic.dataset.load`/`status`, Unload — `cmd.analitic.dataset.unload`). **Redis cache**: stats+coverage+anomalies восстанавливаются при смене symbol/timeframe; ML-детектора, distribution, audit log не кешируются. |

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
