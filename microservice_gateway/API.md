# microservice_gateway API Reference

Документ для frontend-команд и клиентских интеграций.

Цель документа: дать один источник истины по HTTP-контрактам gateway, чтобы фронтенд понимал:

- какие endpoint-ы доступны;
- какие параметры и заголовки нужно передавать;
- какой формат ответа приходит по сети;
- где 200 означает полноценный успех, а где 200 означает degraded/pending state;
- как безопасно строить клиентскую логику без догадок и hardcode.

---

## Scope

Этот документ описывает только HTTP API `microservice_gateway`.

Что не входит в scope:

- login / refresh / register контракты `microservice_account`;
- внутренние Kafka-контракты между сервисами;
- root-level deployment scripts;
- swagger UI как источник истины для frontend-логики.

Если frontend получает JWT от account-сервиса, дальше он работает с gateway по контрактам ниже.

---

## Base URL

Локально по умолчанию при запуске через Docker Compose:

```text
http://localhost:7520
```

Все пути ниже указаны относительно этого base URL.

---

## Integration Checklist

1. Получить access token во внешнем auth-flow account-сервиса.
2. На старте приложения вызвать `GET /api/app/bootstrap`.
3. Для market-экрана сначала вызвать `GET /api/v1/market/config`.
4. Строить запросы `GET /api/v1/market/chart` только значениями из `/api/v1/market/config`.
5. Для защищённых endpoint-ов передавать `Authorization: Bearer <accessToken>`.
6. Не трактовать каждый `200` как «данные полные и готовы»: `dashboard`, `news`, `notifications`, `market/chart` поддерживают degraded/pending состояния внутри тела ответа.

---

## Common Rules

### HTTP conventions

| Правило | Значение |
| ------- | -------- |
| Content-Type у JSON-ответов | `application/json` |
| Health endpoint | plain text |
| Correlation header | `X-Correlation-Id` присутствует в ответах |
| Версионирование | только market API версионирован через `/api/v1/market/*`; остальные endpoint-ы пока без path-version |

### JSON naming

Gateway сериализует JSON в `camelCase`.

Пример:

```json
{
  "apiVersion": "1.0",
  "generatedAt": "2026-05-15T18:22:00Z",
  "degradedServices": []
}
```

### Data types

| Тип данных | Wire format |
| ---------- | ----------- |
| Денежные и рыночные значения | JSON number |
| Даты профиля / новостей / метаданных | ISO 8601 UTC string |
| Рыночные timestamps `t`, `fromMs`, `toMs`, `stepMs` | integer milliseconds UTC |
| UUID | string |

### Auth model

| Тип endpoint-а | Требование |
| -------------- | ---------- |
| Public | токен не нужен |
| Protected | нужен `Authorization: Bearer <JWT>` |
| Optional-auth | endpoint работает без токена, но с токеном может дополнить ответ |

### Degraded vs Error

В gateway есть два разных класса проблем:

| Сценарий | Как выглядит |
| -------- | ------------ |
| Hard error | HTTP `4xx/5xx` |
| Soft degradation | HTTP `200`, но часть данных отсутствует, а тело ответа явно это маркирует |

Frontend должен различать эти сценарии.

Примеры soft degradation:

- `bootstrap`: `degradedServices` не пустой;
- `dashboard`: `meta.degradedSections` не пустой;
- `news` / `notifications`: `degraded = true`;
- `market/chart`: `status = "partial"` или `status = "pending"`.

---

## Error Contract

Для большинства ошибок gateway использует единый JSON-объект:

```json
{
  "status": 401,
  "title": "Unauthorized",
  "code": "auth_required",
  "detail": "Authentication is required.",
  "correlationId": "2de4b66e-8b79-4a3e-b3a1-7e5d65f89e74",
  "timestamp": "2026-05-15T18:22:00Z"
}
```

Поля:

| Поле | Тип | Значение |
| ---- | --- | -------- |
| `status` | number | HTTP status code |
| `title` | string | короткое имя ошибки |
| `code` | string/null | машинно-читаемый код ошибки, если endpoint может различать причины |
| `detail` | string/null | человекочитаемое описание |
| `correlationId` | string/null | идентификатор запроса для трассировки |
| `timestamp` | string | время формирования ответа |

### Admin facade auth errors

`/api/admin/*` — это split-deployment facade для `microservice_admin`.
Эти endpoint-ы требуют shared secret из backend `ADMIN_SHARED_TOKEN`, который
admin-host отправляет как `Authorization: Bearer <token>` или
`X-Admin-Api-Key`.

| HTTP | `code` | Когда возникает |
| ---- | ------ | --------------- |
| `401` | `admin_token_missing` | Запрос пришёл без Bearer token и без `X-Admin-Api-Key` |
| `401` | `admin_token_invalid` | Токен передан, но не совпадает с backend `ADMIN_SHARED_TOKEN` |

Пример mismatch:

```json
{
  "status": 401,
  "title": "Admin Facade Unauthorized",
  "code": "admin_token_invalid",
  "detail": "Admin shared token was rejected by backend. ADMIN_BACKEND_SHARED_TOKEN must match ADMIN_SHARED_TOKEN on backend-host.",
  "correlationId": "9ab711df23904364841b3269dc8f2c2a",
  "timestamp": "2026-05-19T04:20:00Z"
}
```

### Важная оговорка

`GET /api/account/me` при `503 Service Unavailable` сейчас возвращает не `ErrorResponse`, а строку ошибки. Это legacy-несогласованность текущего API, и frontend должен учитывать её отдельно.

---

## Endpoint Catalog

| Method | Path | Auth | Основной сценарий |
| ------ | ---- | ---- | ----------------- |
| GET | `/health` | None | liveness probe |
| GET | `/health/ready` | None | readiness probe (Kafka bootstrap included) |
| GET | `/api/app/bootstrap` | Optional | старт приложения |
| GET | `/api/account/me` | Required | профиль текущего пользователя |
| GET | `/api/dashboard` | Required | главный экран с агрегированными данными |
| GET | `/api/v1/market/config` | None | конфиг market UI |
| GET | `/api/v1/market/chart` | None | свечной график |
| GET | `/api/news` | None | лента новостей |
| GET | `/api/notifications` | Required | уведомления пользователя |

---

## GET /health

### /health: назначение

Быстрая liveness-проверка самого gateway.

### /health: request

```http
GET /health
```

Авторизация не требуется.

### /health: response

`200 OK`

```text
Healthy
```

### /health: frontend notes

- endpoint полезен для ops / monitoring / smoke-check;
- для продуктовой логики фронтенда обычно не нужен.

---

## GET /health/ready

### /health/ready: назначение

Readiness-проверка gateway request/reply path.

В отличие от `/health`, этот endpoint дополнительно проверяет, что bootstrap
listener Kafka/Redpanda доступен по `Kafka:BootstrapServers` и gateway может
делать metadata lookup к broker.

### /health/ready: request

```http
GET /health/ready
```

Авторизация не требуется.

### /health/ready: success response

`200 OK`

```text
Healthy
```

### /health/ready: failure semantics

Если ASP.NET процесс жив, но Kafka bootstrap недоступен, endpoint отвечает
`503 Service Unavailable`.

Это именно тот endpoint, который должны использовать:

- docker/ops health probes gateway;
- split-deployment admin probe;
- мониторинг, которому нужна готовность Kafka-facing команд, а не только live HTTP process.

---

## GET /api/app/bootstrap

### Bootstrap: назначение

Единый стартовый endpoint приложения.

Возвращает:

- краткую информацию о пользователе, если запрос авторизован;
- feature flags;
- системный статус;
- список деградировавших сервисов.

### Bootstrap: how it works

1. Endpoint всегда отвечает `200`, даже без токена.
2. Если токен есть, gateway пытается получить профиль пользователя через account-service.
3. Если account временно недоступен, endpoint всё равно отвечает `200`, но помечает деградацию через `degradedServices` и `systemStatus`.

### Bootstrap: request

```http
GET /api/app/bootstrap
Authorization: Bearer <optional-access-token>
```

### Bootstrap: response example

```json
{
  "user": {
    "id": "1f4d7e6d-15c2-4f70-b22d-a3f0436ce6b7",
    "email": "user@example.com",
    "username": "trader01",
    "status": "active",
    "roles": ["user"],
    "createdAt": "2026-05-01T12:00:00Z"
  },
  "featureFlags": {
    "portfolio": true,
    "market": true,
    "news": true,
    "notifications": true
  },
  "systemStatus": {
    "status": "operational",
    "services": {
      "account": "operational"
    }
  },
  "apiVersion": "1.0",
  "generatedAt": "2026-05-15T18:22:00Z",
  "degradedServices": []
}
```

### Bootstrap: field reference

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `user` | object/null | профиль пользователя; `null`, если запрос без токена |
| `featureFlags` | object | feature toggles для UI |
| `systemStatus.status` | string | `operational` или `degraded` |
| `systemStatus.services` | object | статус downstream-сервисов по имени |
| `apiVersion` | string | версия HTTP-контракта bootstrap |
| `generatedAt` | string | время сборки ответа |
| `degradedServices` | string[] | список сервисов, которые не удалось полноценно опросить |

### Bootstrap: frontend behavior

- если `user == null`, приложение должно считать пользователя неавторизованным;
- если `degradedServices` не пустой, это warning-state, а не hard error;
- `featureFlags` лучше трактовать как server-authoritative переключатели интерфейса.

### Bootstrap: errors

Обычно hard errors не ожидаются. Даже невалидный или отсутствующий токен не приводит к `401`: endpoint остаётся доступным и просто не возвращает пользователя.

---

## GET /api/account/me

### Account Me: назначение

Возвращает полный профиль текущего пользователя.

### Account Me: how it works

1. Gateway валидирует JWT.
2. Из validated token извлекается `sub` / `nameidentifier`.
3. Gateway делает Kafka request/reply в account-service.
4. Если account-service отвечает успешно, фронтенд получает профиль.

### Account Me: request

```http
GET /api/account/me
Authorization: Bearer <access-token>
```

### Account Me: response example

```json
{
  "id": "1f4d7e6d-15c2-4f70-b22d-a3f0436ce6b7",
  "email": "user@example.com",
  "username": "trader01",
  "status": "active",
  "roles": ["user"],
  "createdAt": "2026-05-01T12:00:00Z",
  "updatedAt": "2026-05-01T12:00:00Z"
}
```

### Account Me: field reference

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `id` | string | user id |
| `email` | string | email пользователя |
| `username` | string | отображаемое имя |
| `status` | string | текущий статус аккаунта |
| `roles` | string[] | роли пользователя |
| `createdAt` | string | дата создания |
| `updatedAt` | string | дата обновления |

### Account Me: errors

#### 401 Unauthorized

```json
{
  "status": 401,
  "title": "Unauthorized",
  "detail": "Authentication is required.",
  "correlationId": "2de4b66e-8b79-4a3e-b3a1-7e5d65f89e74",
  "timestamp": "2026-05-15T18:22:00Z"
}
```

#### 503 Service Unavailable

Текущий wire-format:

```json
"Account service timeout"
```

Это строка, а не `ErrorResponse`.

### Account Me: frontend behavior

- использовать для отдельного профиля/кабинета;
- для стартового экрана лучше сначала опираться на `/api/app/bootstrap`;
- 503 нужно обрабатывать отдельно как временную недоступность account downstream.

---

## GET /api/dashboard

### Dashboard: назначение

Возвращает агрегированный main-screen payload.

### Dashboard: how it works

Gateway параллельно запрашивает:

- `portfolio` summary;
- `marketOverview`;
- `trendingAssets`;
- `latestNews`.

Отказ одной секции не валит весь ответ. Endpoint возвращает `200`, а деградация кодируется в `meta.degradedSections`.

### Dashboard: request

```http
GET /api/dashboard
Authorization: Bearer <access-token>
```

### Dashboard: response example

```json
{
  "portfolio": null,
  "marketOverview": null,
  "trendingAssets": [],
  "latestNews": [],
  "meta": {
    "degradedSections": ["portfolio", "market", "news"],
    "generatedAt": "2026-05-15T18:22:00Z"
  }
}
```

### Dashboard: field reference

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `portfolio` | object/null | может быть `null`, если downstream деградировал |
| `marketOverview` | object/null | агрегированный обзор рынка |
| `trendingAssets` | array | список трендовых активов |
| `latestNews` | array | краткие карточки новостей |
| `meta.degradedSections` | string[] | секции, которые не удалось собрать полноценно |
| `meta.generatedAt` | string | время сборки ответа |

### Dashboard: current implementation note

На текущем этапе `portfolio`, `market` и `news` clients реализованы как stubs. Это значит, что frontend должен быть готов часто видеть degraded response даже при HTTP `200`.

### Dashboard: errors

- `401` при отсутствии/невалидности токена;
- `500` только при внутренней необработанной ошибке gateway.

---

## GET /api/v1/market/config

### Market Config: назначение

Server-authoritative конфиг market UI.

Frontend обязан использовать его как источник истины для:

- списка допустимых `symbol`;
- списка допустимых `timeframe`;
- допустимых `limit` по классу таймфрейма;
- значений по умолчанию для первого рендера.

### Market Config: how it works

1. Gateway получает актуальный список символов с Bybit и кэширует результат.
2. Возвращает timeframes, candle grids и defaults в одном ответе.
3. Выставляет `Cache-Control: public, max-age=60, stale-while-revalidate=3540`.

### Market Config: request

```http
GET /api/v1/market/config
```

Авторизация не требуется.

### Market Config: response example

```json
{
  "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  "timeframes": [
    {
      "id": "1m",
      "label": "1 min",
      "class": "heavy",
      "stepMs": 60000
    },
    {
      "id": "5m",
      "label": "5 min",
      "class": "heavy",
      "stepMs": 300000
    },
    {
      "id": "1d",
      "label": "1 day",
      "class": "light",
      "stepMs": 86400000
    }
  ],
  "candleCounts": {
    "heavy": [50, 100, 200, 500],
    "medium": [50, 100, 200, 500, 1000],
    "light": [50, 100, 200, 500, 1000, 2000],
    "heavyTimeframes": ["1m", "3m", "5m"],
    "mediumTimeframes": ["15m", "30m", "60m", "120m", "240m"],
    "lightTimeframes": ["360m", "720m", "1d"]
  },
  "defaults": {
    "symbol": "BTCUSDT",
    "timeframe": "5m",
    "candleCount": 200
  },
  "cachedAt": "2026-05-15T18:22:00Z",
  "symbolsUpdatedAt": "2026-05-15T18:00:00Z"
}
```

### Market Config: field reference

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `symbols` | string[] | разрешённые торговые пары |
| `timeframes` | object[] | все поддерживаемые таймфреймы |
| `timeframes[].class` | string | `heavy`, `medium`, `light` |
| `timeframes[].stepMs` | number | длина свечи в миллисекундах |
| `candleCounts` | object | допустимые `limit` по классу таймфрейма |
| `defaults` | object | стартовые значения UI |
| `cachedAt` | string | когда gateway сформировал этот ответ |
| `symbolsUpdatedAt` | string | когда обновлялся список символов |

### Market Config: frontend behavior

- не хардкодить символы, таймфреймы и сетки `limit`;
- сначала выбрать timeframe, потом брать допустимые `limit` из соответствующего массива `heavy` / `medium` / `light`;
- разумно кэшировать ответ на клиенте в рамках сессии.

---

## GET /api/v1/market/chart

### Market Chart: назначение

Возвращает OHLCV candles для выбранной пары, таймфрейма и количества свечей.

### Market Chart: how it works

1. Gateway валидирует `symbol`, `timeframe`, `limit`.
2. Проверяет hot-window cache по ключу `(symbol, timeframe, limit)`.
3. Проверяет ingest lock.
4. Читает coverage и rows из data-service.
5. Если символ известен gateway, но локального окна не хватает, gateway создаёт `ingest` job через `cmd.data.dataset.jobs.start` и синхронно ждёт terminal status через `cmd.data.dataset.jobs.get`.
6. Этот lazy hydrate попадает в тот же `DatasetJobRunner`, что и admin queue: максимум 4 одновременных ingest job-а, при этом две job для одного `target_table` одновременно не исполняются.
7. Если ingest lock уже занят другим запросом, queued job завершился ошибкой/timeout или data-service ответил `claim_check`, gateway возвращает fallback-состояние `partial` или `pending`.
8. Возвращает одно из состояний: `ok`, `partial`, `pending`.

### Market Chart: request

```http
GET /api/v1/market/chart?symbol=BTCUSDT&timeframe=5m&limit=200
```

Авторизация не требуется.

### Market Chart: query parameters

| Параметр | Тип | Обязателен | Правило |
| -------- | --- | ---------- | ------- |
| `symbol` | string | yes | должен входить в `symbols` из `/api/v1/market/config` |
| `timeframe` | string | yes | должен входить в `timeframes[].id` из `/api/v1/market/config` |
| `limit` | number | yes | должен входить в grid для класса выбранного timeframe |

Если `symbol` отсутствует в `/api/v1/market/config`, gateway вернёт `400 INVALID_SYMBOL` и не будет запускать ingest. Если `symbol` валиден, но таблицы/окна ещё нет в Postgres, gateway сначала попробует лениво гидрировать нужную часть датасета через dataset jobs queue, а уже потом ответить графиком.

### Market Chart: success response example (`status = "ok"`)

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "5m",
  "limit": 200,
  "candles": [
    {
      "t": 1715788200000,
      "o": 62000.12,
      "h": 62150.55,
      "l": 61980.01,
      "c": 62110.42,
      "v": 135.42,
      "tv": 8401123.22
    },
    {
      "t": 1715788500000,
      "o": 62110.42,
      "h": 62180.00,
      "l": 62050.11,
      "c": 62070.33,
      "v": 98.15,
      "tv": 6098812.91
    }
  ],
  "meta": {
    "requested": 200,
    "available": 200,
    "fromMs": 1715728500000,
    "toMs": 1715788500000,
    "coverage": "full"
  },
  "status": "ok",
  "retryAfterMs": null
}
```

### Market Chart: pending response example (`status = "pending"`, fallback path)

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "5m",
  "limit": 200,
  "candles": [],
  "meta": {
    "requested": 200,
    "available": 0,
    "fromMs": 0,
    "toMs": 0,
    "coverage": "pending"
  },
  "status": "pending",
  "retryAfterMs": 5000
}
```

### Market Chart: field reference

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `symbol` | string | запрошенная пара |
| `timeframe` | string | запрошенный timeframe id |
| `limit` | number | запрошенный limit |
| `candles` | array | свечи в ascending time order |
| `candles[].t` | number | timestamp открытия свечи в ms UTC |
| `candles[].o/h/l/c` | number | open/high/low/close |
| `candles[].v` | number | volume |
| `candles[].tv` | number | turnover |
| `meta.requested` | number | сколько свечей просили |
| `meta.available` | number | сколько свечей реально вернулось |
| `meta.coverage` | string | `full`, `partial`, `pending`, `empty` |
| `status` | string | `ok`, `partial`, `pending` |
| `retryAfterMs` | number/null | сколько ждать до следующего запроса |

### Market Chart: frontend behavior by status

| `status` | Что делать на фронте |
| -------- | -------------------- |
| `ok` | рендерить график без повторного опроса |
| `partial` | рендерить уже пришедшие свечи и запланировать retry через `retryAfterMs` |
| `pending` | показать skeleton / loading state и повторить запрос через `retryAfterMs` |

### Market Chart: important notes

- не пытаться самостоятельно вычислять допустимые `limit`;
- сначала всегда использовать `/api/v1/market/config`;
- частые запросы одного и того же latest-window, например `ETHUSDT + 15m + 100`, gateway кэширует по ключу `(symbol, timeframe, limit)`, поэтому repeated refresh обычно уходит без повторного похода в data-service;
- `pending` и `partial` — это штатные продуктовые состояния, а не hard error; теперь они означают, что текущий запрос не смог сам закончить lazy hydrate окна;
- lazy hydrate больше не обходит dataset jobs: он идёт через `cmd.data.dataset.jobs.start/get`, поэтому соблюдает общий ingest queue cap `4` и per-table сериализацию внутри data-service;
- если data-service вернул `claim_check` для слишком большого payload, gateway сейчас **не** скачивает claim-check объект напрямую, а отдаёт `pending`-подобный retry scenario. Поэтому для UI безопаснее уменьшить `limit`, если этот кейс повторяется.

### Market Chart: errors

#### 400 Bad Request

```json
{
  "status": 400,
  "title": "Bad Request",
  "detail": "INVALID_LIMIT: 150 is not in the allowed candle count grid for '5m' (Heavy). Allowed: [50, 100, 200, 500]",
  "correlationId": "2de4b66e-8b79-4a3e-b3a1-7e5d65f89e74",
  "timestamp": "2026-05-15T18:22:00Z"
}
```

Типовые причины:

- неизвестный `symbol`;
- неизвестный `timeframe`;
- `limit` не входит в server-authoritative grid.

---

## GET /api/news

### News: назначение

Возвращает список новостей для публичной ленты.

### News: how it works

1. Принимает `limit`.
2. Clamp-ит его в диапазон `1..100`.
3. Запрашивает news client.
4. Даже при недоступности downstream отвечает `200`, но ставит `degraded = true`.

### News: request

```http
GET /api/news?limit=20
```

Авторизация не требуется.

### News: query parameters

| Параметр | Тип | Default | Правило |
| -------- | --- | ------- | ------- |
| `limit` | number | `20` | clamp `1..100` |

### News: response example

```json
{
  "items": [],
  "total": 0,
  "degraded": true
}
```

### News: field reference

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `items` | array | новости |
| `total` | number | число элементов в `items` |
| `degraded` | boolean | downstream news currently unavailable |

### News: current implementation note

Текущий `NewsServiceClient` — stub. Поэтому frontend должен быть готов к `degraded = true` и пустому массиву даже при HTTP `200`.

---

## GET /api/notifications

### Notifications: назначение

Возвращает уведомления текущего пользователя.

### Notifications: how it works

1. Требует JWT.
2. Из токена берётся user id.
3. `limit` clamp-ится в диапазон `1..100`.
4. При недоступности downstream endpoint всё равно отвечает `200`, но возвращает `degraded = true`.

### Notifications: request

```http
GET /api/notifications?limit=50
Authorization: Bearer <access-token>
```

### Notifications: query parameters

| Параметр | Тип | Default | Правило |
| -------- | --- | ------- | ------- |
| `limit` | number | `50` | clamp `1..100` |

### Notifications: response example

```json
{
  "items": [],
  "unreadCount": 0,
  "degraded": true
}
```

### Notifications: field reference

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `items` | array | список уведомлений |
| `unreadCount` | number | число непрочитанных уведомлений |
| `degraded` | boolean | уведомления недоступны, но UI может показать fallback state |

### Notifications: item shape

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `id` | string | идентификатор уведомления |
| `type` | string | например `price_alert`, `system`, `news` |
| `title` | string | заголовок |
| `body` | string/null | текст уведомления |
| `isRead` | boolean | признак прочтения |
| `createdAt` | string | время создания |

### Notifications: current implementation note

Текущий `NotificationsServiceClient` — stub. Поэтому `degraded = true` и пустой список — ожидаемый текущий сценарий.

---

## Frontend Recommendations

### Startup flow

1. Безусловно вызвать `/api/app/bootstrap`.
2. Если есть token, передать его туда же.
3. Для графика сначала вызвать `/api/v1/market/config`, потом `/api/v1/market/chart`.
4. Не блокировать весь UI из-за degraded частей `dashboard` или `news`.

### Retry policy

- повторять `market/chart` только если сервер сам вернул `retryAfterMs`;
- не ставить агрессивный polling для `bootstrap`, `news`, `notifications`;
- `dashboard` разумно обновлять по screen refresh, а не по tight interval.

### Fallback UX

| Сценарий | Рекомендуемый UX |
| -------- | ---------------- |
| `bootstrap.user == null` | guest-mode / экран логина |
| `dashboard.meta.degradedSections` не пустой | рендерить доступные карточки, а не падать целым экраном |
| `news.degraded == true` | empty state «новости временно недоступны» |
| `notifications.degraded == true` | empty state «уведомления временно недоступны» |
| `market/chart.status == pending` | skeleton + retry; обычно это означает, что lazy hydrate ещё не завершился или уже выполняется другим запросом |
| `market/chart.status == partial` | график на частичных данных + non-blocking retry |

---

## Change Policy

Если меняются:

- route;
- auth requirement;
- параметры запроса;
- wire-format JSON;
- degraded/pending semantics;
- polling contract;
- error body,

то этот файл должен обновляться одновременно с кодом.
