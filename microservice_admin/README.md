# microservice_admin

**Роль:** Admin UI платформы ModelLine. Next.js 14 (App Router). Коммуницирует с `microservice_data` и `microservice_analitic` **исключительно через Kafka** (Redpanda). Никакого прямого HTTP между application-сервисами.

**Стек:** Next.js 14, React 18, TypeScript 5, Tailwind CSS 3, shadcn/ui, Radix UI, kafkajs, recharts  
**Конфиги:** `tailwind.config.js` + `postcss.config.js` (последний критичен — регистрирует `tailwindcss` и `autoprefixer` как PostCSS плагины, без него `@tailwind` директивы не обрабатываются)  
**Порт:** `3000`  
**Base path:** `/admin` — приложение обслуживается по пути `/admin` (настроено через `basePath` и `assetPrefix` в `next.config.js`). Nginx пробрасывает `sha-trade.tech/admin` → `admin:3000`. Статика `_next/static/*` тоже проксируется корректно. Next.js **не** применяет `basePath` автоматически к `fetch()` и `EventSource` — все клиентские обращения к API используют `process.env.NEXT_PUBLIC_BASE_PATH ?? ''` как префикс (`healthClient.ts`, `kafkaClient.ts`, `useEvents.ts`).
**Зависимости:** `microservice_infra` (Redpanda broker)

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

Тот же паттерн применяется в двух местах: `src/lib/kafka.ts` (reply-inbox для
request-reply) и `src/app/api/events/route.ts` (EVT_* топики для SSE). В обоих:

1. Consumer создаётся с `allowAutoTopicCreation: false` — KafkaJS не просит брокера авто-создавать топик.
2. Перед `consumer.connect()` все нужные топики явно создаются через
   `admin.createTopics({ topics: [...], waitForLeaders: false })`
   (`numPartitions: 1`, `replicationFactor: 1`). `waitForLeaders: false` —
   Redpanda v24 возвращает неконсистентный ответ при `true`, и KafkaJS бросает
   исключение, хотя топик реально создаётся. `TOPIC_ALREADY_EXISTS` (код 36)
   тихо игнорируется как идемпотентный, прочие ошибки логируются через
   `console.warn` и не пробрасываются.
3. После `admin.disconnect()` — пауза (500 мс в `kafka.ts`, 300 мс в SSE
   handler), за это время single-node Redpanda проводит leader election, и
   `consumer.connect()` уже не получает `NOT_LEADER_OR_FOLLOWER`.

Без п.2/3 в SSE-хендлере KafkaJS слал `MetadataRequest v6` на несуществующие
`EVT_*`-топики, Redpanda отвечала `INVALID_PARTITIONS`, стрим падал и
браузер переподключался каждые несколько секунд.

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

**Fluid typography (`--font-size-*`):** CSS custom properties с `clamp()`, минимум при 1280px, максимум при 2560px. Семь ступеней: `xs` / `sm` / `base` / `lg` / `xl` / `2xl` / `3xl`. Переопределяют Tailwind-утилиты `.text-*` через `@layer utilities` (более поздний source-order).

**Шрифт:** Inter (загружен через `next/font/google`)

**Брейкпоинты:**
- Стандартные Tailwind: `sm` 640 px, `md` 768 px, `lg` 1024 px, `xl` 1280 px, `2xl` 1536 px
- Кастомные: `3xl` 1920 px (Full HD), `4xl` 2560 px (4K)

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
- `GET /api/events` — SSE Route Handler. Подписывается на все `EVT_*` Kafka-топики через отдельный consumer (unique group ID per connection). Стримит JSON `{ type, payload }` в формате SSE.
- `src/hooks/useEvents.ts` — React hook `useEvents(handlers)`. Открывает `EventSource` на mount, диспатчит payload в нужный handler, закрывает на unmount. Handlers хранятся в ref — не вызывает реконнект при перерендере.

## Компоненты UI

- **`src/components/Sidebar.tsx`** — сворачиваемая боковая навигация. Два состояния: `w-56` (развёрнут) / `w-14` (свёрнут), анимировано через `transition-all duration-200`. Кнопка ChevronLeft/Right для переключения; состояние сохраняется в `localStorage` (`modelline:sidebar:collapsed`). Логотип с lucide-react `Zap`. При развёрнутом sidebar — пульсирующий dot Kafka-статуса. Nav-иконки показывают `title` tooltip при свёрнутом состоянии. Kafka healthcheck на mount, повторяется каждые 30 с.
- **`src/components/Toast.tsx`** — глобальные toast-уведомления. Экспортирует хук `useToast()` и провайдер `ToastProvider`. Типы: `success`, `error`, `info`. Автозакрытие через 4 с.

## Страницы

| Страница | Описание |
|----------|----------|
| `/` (Dashboard) | Bento Grid: Row 1 — 4 StatCard с `border-l-4 border-l-{color}` акцентами (`grid-cols-2 xl:grid-cols-4`). Row 2 — 2 колонки: стек из 4 ServiceCard (2 application через Kafka: `data`, `analitic`; 2 infra через HTTP `/api/health`: `Redpanda`, `MinIO`) + `CoverageBar` (recharts BarChart horizontal). Row 3 — shadcn Table датасетов с `pct.toFixed(1)%`. Кнопка Refresh обновляет все карточки одновременно. |
| `/download` | 2-колоночный layout (`lg:grid-cols-[380px,1fr]`): левая — Dataset Configuration (Select/Input/кнопки); правая — Coverage Card с `CoverageBar` (один бар) + 3 stat строки, появляется после Check Coverage. Ниже: Available Tables + Action History на всю ширину. Таймфрейм `ALL` поддерживается в `handleIngest` (sequential loop), `handleCheckCoverage` (parallel) и `handleDeleteRows` (sequential loop с confirm-диалогом). `handleDeleteRows` при `ALL`: confirm упоминает все таймфреймы, удаляет последовательно через `CMD_DATA_DATASET_DELETE_ROWS`, ошибка отдельного TF → info-toast, итог — success-toast с суммой строк и числом TF. |
| `/train` | Кастомный tab-switcher в header (без Radix Tabs). 2-колоночный grid на `lg+`: левая — Config + Status Card с `ProgressLine` (recharts LineChart, показывается после ≥2 точек прогресса); правая — Training History table. `progressHistory` state сбрасывается при каждом новом запуске. |
| `/compare` | CSS grid 2 колонки, shadcn Card в каждой, shadcn Select, Button экспорта |
| `/anomaly` | Инспекция / очистка / обработка датасетов. 4 свёртываемых секции (Inspect / Anomalies / Clean / Process). Stage 1 (Inspect): df.info-таблица + lazy-гистограммы численных колонок. Anomalies/Clean/Process — «Coming soon» (см. roadmap). |

## Roadmap

- ✅ **Step 1:** skeleton with health dashboard.
- ✅ **Step 1.5:** Kafka-only IPC, HTTP clients deleted.
- ✅ **Step 2:** UI redesign — Sidebar, Toast, utility CSS.
- ✅ **Step 3:** Design system v2 — stale-while-revalidate, Quick Stats.
- ✅ **Step 4:** shadcn/ui overhaul — Radix UI primitives, CSS variable dark theme, full component library, all pages rewritten.
- ✅ **Step 5:** UI redesign v2 — recharts charts (CoverageBar, ProgressLine), bento-grid Dashboard, 2-col layouts, collapsible Sidebar, CSS contrast fixes.
- ⏳ **Step 6:** migrate `frontend/pages/*` here; each former backend call becomes a Kafka command via `services/messaging.py`.
