# microservice_gateway API Reference

Документ для frontend-команд и HTTP-интеграций с gateway, включая mobile/public API и backend facade для `microservice_admin`.

Цель документа: дать один источник истины по HTTP-контрактам gateway, чтобы фронтенд понимал:

- какие endpoint-ы доступны;
- какие параметры и заголовки нужно передавать;
- какой формат ответа приходит по сети;
- где 200 означает полноценный успех, а где 200 означает degraded/pending state;
- как безопасно строить клиентскую логику без догадок и hardcode.

---

## Scope

Этот документ описывает HTTP API `microservice_gateway` в двух плоскостях:

- public/mobile routes (`/api/*`, `/health*`);
- admin backend facade (`/api/admin/*`) для local/split интеграции `microservice_admin`.

Что не входит в scope:

- внутренние детали реализации `microservice_account` вне gateway proxy; для `POST /api/account/{register,login,refresh,logout}` этот документ фиксирует только wire-compatible HTTP contract gateway;
- внутренние Kafka-контракты между сервисами;
- shape успешных downstream payload-ов за `/api/admin/*` глубже gateway-level HTTP semantics: gateway там прозрачно проксирует JSON владельца сервиса;
- root-level deployment scripts;
- swagger UI как источник истины для frontend-логики.

Если mobile/frontend получает JWT от account-сервиса, дальше он работает с gateway по контрактам ниже. Если `microservice_admin` работает через backend facade, он использует только HTTP `POST /api/admin/*` и не ходит в Kafka напрямую.

---

## Base URL

| Surface | Example URL |
| ------- | ----------- |
| Public/mobile API (local/direct) | `http://localhost:7520` |
| Admin facade (local/direct) | `http://localhost:7520/api/admin/*` |
| Admin facade (split backend-host) | `https://<backend-host>:8443/api/admin/*` |

Все public/mobile пути ниже указаны относительно base URL gateway. Для admin facade ниже указаны полные route-prefix'ы `/api/admin/*`, потому что этот surface может жить и на прямом host-порту gateway, и за split TLS facade `:8443`.

---

## Integration Checklist

1. Получить access token через `POST /api/account/login` на gateway или напрямую у account-сервиса.
2. На старте приложения вызвать `GET /api/app/bootstrap`.
3. Для market-экрана сначала вызвать `GET /api/v1/market/config`.
4. Строить запросы `GET /api/v1/market/chart` только значениями из `/api/v1/market/config`.
5. Для защищённых endpoint-ов передавать `Authorization: Bearer <accessToken>`.
6. Не трактовать каждый `200` как «данные полные и готовы»: `dashboard`, `market/chart` и часть lightweight frontend routes могут возвращать fallback/cache-backed payload, а не durable downstream truth.
7. Если интеграция идёт из `microservice_admin`, использовать только `POST /api/admin/*` с `Authorization: Bearer <admin JWT>`; JWT должен быть выпущен Account Service для login-only пользователя с ролью `admin`. Успешный JSON gateway не нормализует, а возвращает как ответ владельца Kafka-команды.

---

## Common Rules

### HTTP conventions

| Правило | Значение |
| ------- | -------- |
| Content-Type у JSON-ответов | `application/json` |
| Health endpoint | plain text |
| Correlation header | `X-Correlation-Id` присутствует в ответах |
| Версионирование | только market API версионирован через `/api/v1/market/*`; остальные endpoint-ы пока без path-version |

### Public cache semantics

| Route | Текущее поведение |
| ----- | ----------------- |
| `GET /api/v1/market/config` | `Cache-Control: public, max-age=60, stale-while-revalidate=3540` |
| `GET /api/v1/market/overview` | `Cache-Control: public, max-age=30, stale-while-revalidate=120` |
| `GET /api/v1/market/tickers` | `Cache-Control: public, max-age=15, stale-while-revalidate=45` |
| `POST /api/v1/market/quotes/batch` | `Cache-Control: public, max-age=10, stale-while-revalidate=20` |
| `GET /api/v1/market/converter/quote` | `Cache-Control: public, max-age=10, stale-while-revalidate=20` |
| `GET /api/news` and `GET /api/news/home` | `Cache-Control: public, max-age=30, stale-while-revalidate=300` |

ETag/If-None-Match semantics для этих routes пока не реализованы. Frontend и edge cache должны опираться именно на `Cache-Control` и timestamps в payload.

### Browser CORS rules

| Сценарий | Текущее поведение gateway |
| -------- | ------------------------- |
| Public/protected client routes (`/api/*`, кроме `/api/admin/*`) | gateway отвечает `Access-Control-Allow-*` и допускает browser cross-origin requests |
| Preflight `OPTIONS` для JWT-protected routes (`/api/dashboard`, и т.п.) | обрабатывается CORS middleware до auth, поэтому browser не упирается в `405` или raw auth challenge |
| `/api/admin/*` | CORS намеренно отключён: этот surface рассчитан на server-to-server use из `microservice_admin`, а не на browser JavaScript |

Если web-клиент работает по `https`, а backend URL остаётся `http`, browser policy mixed-content по-прежнему может блокировать вызов даже при корректном CORS. В таком случае нужен HTTPS или same-origin proxy path.

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

Для mobile API фактическая модель доступа сейчас такая:

- `guest` = анонимный вызов без JWT. В `microservice_account` есть role-code `guest` для общей модели, но gateway трактует guest как отсутствие токена, а не как persisted user.
- `user` = валидный Bearer JWT с пользовательскими claims.

Из этого следует практическое правило: public/optional routes отдают общие рыночные данные гостю, а personal routes остаются только для `user`.

### Admin facade conventions

| Правило | Значение |
| ------- | -------- |
| Method | все `/api/admin/*` endpoint-ы используют `POST`, даже когда операция логически read-only |
| Request body | gateway принимает JSON body и проксирует его в Kafka payload без переформатирования; если payload не нужен, можно послать `{}` |
| Success payload | `200 OK` возвращает JSON downstream-владельца как есть, без envelope/wrapper от gateway |
| Auth | `Authorization: Bearer <admin JWT>` |
| Gateway-managed failures | `401`, `503`, `504` нормализуются в `ErrorResponse` |
| Browser CORS | intentionally disabled |

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

### Gateway-local frontend state

Часть mobile/BFF routes сейчас обслуживается самим gateway без отдельного owner-service и хранится в gateway-owned cache-backed state:

- `GET /api/portfolio/summary`;
- `/api/exchanges/*`;
- `/api/alerts/*`;
- `/api/services/toggles`;
- `GET /api/admin/{summary,users,services,statistics}`.

Практические последствия:

- state хранится через `IFrontendContractState` в `IDistributedCache`;
- если настроен Redis, linked exchanges / alerts / toggles и lightweight mobile-admin counters переживают restart и становятся доступны другим gateway instances;
- если Redis не настроен, gateway использует `AddDistributedMemoryCache`, и тогда поведение остаётся локальным для текущего процесса;
- записи маленькие, а model обновления intentionally простая: last-write-wins.

Эти routes уже пригодны как стабильный frontend contract, но при memory-only fallback по-прежнему не становятся cross-process persistent source of truth.

---

## Error Contract

Для большинства ошибок gateway использует единый JSON-объект:

```json
{
  "status": 401,
  "title": "Unauthorized",
  "code": null,
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

### Admin facade-specific errors

`/api/admin/*` — это split-deployment facade для `microservice_admin`.
Эти endpoint-ы требуют JWT от Account Service с ролью `admin`. Admin UI получает
его через login-only admin account и пересылает в gateway как Bearer token;
UID в теле запроса не используется как источник прав.

| HTTP | `code` | Когда возникает |
| ---- | ------ | --------------- |
| `401` | `null` | Запрос пришёл без валидного Bearer JWT |
| `403` | `null` | JWT валиден, но у пользователя нет роли `admin` |
| `503` | `admin_kafka_unavailable` | Gateway не смог отправить Kafka request: broker/connectivity/bootstrap path недоступен |
| `504` | `admin_kafka_timeout` | Gateway не смог завершить Kafka request/reply в timeout окна route: либо reply inbox ещё не ready, либо downstream reply не пришёл вовремя |

Смысл `503` и `504` у admin facade разный:

- `503 admin_kafka_unavailable` означает transport failure до успешного publish в Kafka;
- `504 admin_kafka_timeout` означает, что gateway не получил рабочий request/reply path в пределах timeout окна: это может быть либо pre-publish состояние `reply inbox not ready`, либо уже отправленный request без downstream reply.

Для split admin path gateway теперь считает reply path ready только после
реального assignment reply-inbox consumer-а. Это убирает ложный startup-state,
когда backend успевал принять HTTP admin facade call, но reply inbox ещё не
существовал или не был назначен consumer-у.

Пример отсутствующей admin-сессии:

```json
{
  "status": 401,
  "title": "Unauthorized",
  "code": null,
  "detail": "Authentication is required.",
  "correlationId": "9ab711df23904364841b3269dc8f2c2a",
  "timestamp": "2026-05-19T04:20:00Z"
}
```

### Auth/profile error note

`GET /api/account/me` и gateway-managed ошибки auth proxy теперь используют общий `ErrorResponse` JSON-contract. Для auth proxy это касается gateway-managed fail-path-ов: network/downstream-unavailable и downstream non-success statuses нормализуются в envelope с `code = "account_proxy_error"`, а успешные payload-ы всё так же проксируются как есть.

---

## Endpoint Catalog

| Method | Path | Auth | Основной сценарий |
| ------ | ---- | ---- | ----------------- |
| GET | `/health` | None | liveness probe |
| GET | `/health/ready` | None | readiness probe (Kafka bootstrap included) |
| GET | `/api/app/bootstrap` | Optional | старт приложения |
| POST | `/api/account/register` | None | register через gateway auth proxy |
| POST | `/api/account/login` | None | login через gateway auth proxy |
| POST | `/api/account/refresh` | None | refresh через gateway auth proxy |
| POST | `/api/account/logout` | Required | logout через gateway auth proxy |
| GET | `/api/account/me` | Required | профиль текущего пользователя |
| GET | `/api/dashboard` | Optional | главный экран с агрегированными данными; guest получает только public sections |
| GET | `/api/v1/market/overview` | None | публичный home-screen overview |
| GET | `/api/v1/market/tickers` | None | searchable/sortable/paginated market snapshot list |
| POST | `/api/v1/market/quotes/batch` | None | batch quote refresh по списку symbol-ов |
| GET | `/api/v1/market/converter/quote` | None | quote для asset-to-asset conversion |
| GET | `/api/v1/market/config` | None | конфиг market UI |
| GET | `/api/v1/market/chart` | None | свечной график |
| GET | `/api/portfolio/summary` | Required | расширенная сводка портфеля |
| GET | `/api/exchanges/available` | Required | каталог поддерживаемых бирж |
| GET | `/api/exchanges/linked` | Required | связанные пользователем биржи |
| POST | `/api/exchanges/link` | Required | создать/перепривязать linked exchange |
| PATCH | `/api/exchanges/link/{slug}` | Required | обновить linked exchange |
| DELETE | `/api/exchanges/link/{slug}` | Required | удалить linked exchange |
| GET | `/api/alerts` | Required | список ценовых алертов |
| POST | `/api/alerts` | Required | создать ценовой алерт |
| PATCH | `/api/alerts/{id}` | Required | обновить ценовой алерт |
| DELETE | `/api/alerts/{id}` | Required | удалить ценовой алерт |
| GET | `/api/services/toggles` | Required | получить service toggles для settings UI |
| PATCH | `/api/services/toggles` | Required | частично обновить service toggles |
| GET | `/api/news` | None | лента новостей |
| GET | `/api/news/home` | None | compact home-screen news feed |
| GET | `/api/notifications` | Required | уведомления пользователя |
| GET | `/api/admin/summary` | Admin JWT | lightweight mobile-admin summary |
| GET | `/api/admin/users` | Admin JWT | lightweight mobile-admin users view |
| GET | `/api/admin/services` | Admin JWT | lightweight mobile-admin services view |
| GET | `/api/admin/statistics` | Admin JWT | lightweight mobile-admin statistics |
| POST | `/api/admin/health/*` | Admin JWT | backend facade для health-check команд admin |
| POST | `/api/admin/dataset/*` | Admin JWT | backend facade для dataset/data-service команд |
| POST | `/api/admin/market-watcher/*` | Admin JWT | backend facade для dedicated market watcher control-plane |
| POST | `/api/admin/analytic/*` | Admin JWT | backend facade для analytic dataset/anomaly команд |
| POST | `/api/admin/analytics/*` | Admin JWT | backend facade для ML train/model/predict команд |

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
listener Kafka/Redpanda доступен по `Kafka:BootstrapServers`, gateway может
делать metadata lookup к broker и у live `KafkaRequestClient` уже назначен
consumer на его `reply.gateway.{instanceId}` inbox.

Практически это означает: `7520/health/ready = 200` нужен раньше, чем можно
считать Kafka-backed `/api/admin/*` routes рабочими. Этот статус теперь
означает не просто доступность broker metadata, а готовность bootstrap +
reply inbox request/reply path. Backend `:8443/health/ready`
должен просто проксировать этот endpoint; если facade на `8443` отдаёт `404`,
это признак stale infra nginx deploy, а не проблемы admin-host edge.

### /health/ready: request

```http
GET /health/ready
```

Авторизация не требуется.

### /health/ready: success response

`200 OK`

```json
{
  "status": "Healthy",
  "totalDurationMs": 12,
  "checks": {
    "self": {
      "status": "Healthy",
      "description": "Healthy",
      "durationMs": 0
    },
    "kafka": {
      "status": "Healthy",
      "description": "Kafka bootstrap listener is reachable.",
      "durationMs": 4
    },
    "kafka-request-reply": {
      "status": "Healthy",
      "description": "Kafka reply inbox 'reply.gateway.ab12cd34' is assigned and ready. Last state: assigned to partitions: reply.gateway.ab12cd34 [0]",
      "durationMs": 1
    }
  }
}
```

### /health/ready: failure response

`503 Service Unavailable`

```json
{
  "status": "Unhealthy",
  "totalDurationMs": 15,
  "checks": {
    "kafka-request-reply": {
      "status": "Unhealthy",
      "description": "Kafka reply inbox 'reply.gateway.ab12cd34' is not assigned yet. Last state: reply inbox topic create exceeded startup budget; trying bootstrap produce",
      "durationMs": 1
    }
  }
}
```

### /health/ready: failure semantics

Если ASP.NET процесс жив, но Kafka bootstrap недоступен или reply inbox ещё
не готов, endpoint отвечает `503 Service Unavailable`. JSON body теперь
показывает per-check descriptions, поэтому `curl http://<host>:7520/health/ready`
сразу раскрывает текущую причину неготовности.

Это именно тот endpoint, который должны использовать:

- docker/ops health probes gateway;
- split-deployment admin probe;
- мониторинг, которому нужна готовность Kafka-facing команд, а не только live HTTP process.

---

## POST /api/admin/*

### Admin facade: назначение

`/api/admin/*` — это HTTP facade для `microservice_admin` и других ops-интеграций, которым нельзя или не нужно работать с Kafka напрямую.

Gateway остаётся единственной HTTP→Kafka точкой входа: он проверяет JWT и роль `admin`, проставляет correlation id, публикует Kafka request/reply и возвращает downstream JSON как обычный HTTP-ответ.

### Admin facade: auth and transport rules

```http
POST /api/admin/<route>
Authorization: Bearer <admin JWT>
Content-Type: application/json
```

Authorization должен содержать admin JWT, полученный через Account Service login.

Правила:

- success-ответ всегда `200 OK` с JSON downstream-владельца, без дополнительного envelope от gateway;
- большинство route-ов используют обычный backend timeout, а тяжёлые route-ы вроде export/ingest/import/anomaly/train/predict/load-ohlcv идут через удлинённый timeout, но для клиента контракт остаётся тем же: `200` / `401` / `403` / `503` / `504`;
- если route не требует payload, безопасно отправлять `{}`;
- correlation id так же возвращается в `X-Correlation-Id`.

### Admin facade: route map

#### Health

| Method | Route | Downstream intent |
| ------ | ----- | ----------------- |
| POST | `/api/admin/health/data` | proxy к `cmd.data.health` |
| POST | `/api/admin/health/analytics` | proxy к `cmd.analytics.health` |

#### Dataset / data-service

| Method | Route | Downstream intent |
| ------ | ----- | ----------------- |
| POST | `/api/admin/dataset/list-tables` | список dataset tables |
| POST | `/api/admin/dataset/coverage` | coverage по таблице/окну |
| POST | `/api/admin/dataset/rows` | получить rows диапазона |
| POST | `/api/admin/dataset/export` | инициировать CSV/ZIP export |
| POST | `/api/admin/dataset/ingest` | one-shot ingest через data-service |
| POST | `/api/admin/dataset/normalize-timeframe` | normalize timeframe id |
| POST | `/api/admin/dataset/make-table-name` | canonical table naming |
| POST | `/api/admin/dataset/instrument-details` | instrument metadata |
| POST | `/api/admin/dataset/schema` | schema/columns таблицы |
| POST | `/api/admin/dataset/find-missing` | поиск gaps в датасете |
| POST | `/api/admin/dataset/timestamps` | список timestamps |
| POST | `/api/admin/dataset/constants` | dataset constants/config |
| POST | `/api/admin/dataset/delete-rows` | удалить строки по критерию |
| POST | `/api/admin/dataset/import-csv` | импорт CSV в data-service |
| POST | `/api/admin/dataset/upsert-ohlcv` | точечный upsert raw OHLCV |
| POST | `/api/admin/dataset/column-stats` | column statistics |
| POST | `/api/admin/dataset/column-histogram` | histogram численной колонки |
| POST | `/api/admin/dataset/browse` | paginated browse сырого набора |
| POST | `/api/admin/dataset/compute-features` | пересчёт derived features |
| POST | `/api/admin/dataset/detect-anomalies` | anomaly detection |
| POST | `/api/admin/dataset/clean-preview` | preview dataset clean-up |
| POST | `/api/admin/dataset/clean-apply` | apply dataset clean-up |
| POST | `/api/admin/dataset/audit-log` | audit log операций над dataset |
| POST | `/api/admin/dataset/jobs/start` | создать queued dataset job |
| POST | `/api/admin/dataset/jobs/cancel` | отменить dataset job |
| POST | `/api/admin/dataset/jobs/get` | получить один dataset job |
| POST | `/api/admin/dataset/jobs/list` | список dataset jobs |
| POST | `/api/admin/dataset/db-ping` | DB connectivity ping |

#### Dedicated market watcher

| Method | Route | Downstream intent |
| ------ | ----- | ----------------- |
| POST | `/api/admin/market-watcher/status` | runtime snapshot dedicated watcher-а |
| POST | `/api/admin/market-watcher/set-enabled` | включить/выключить watcher без рестарта |
| POST | `/api/admin/market-watcher/rows` | paged realtime rows из `market_watch_live` |
| POST | `/api/admin/market-watcher/logs` | watcher-only runtime log stream |

#### Analitic / dataset session / anomaly

| Method | Route | Downstream intent |
| ------ | ----- | ----------------- |
| POST | `/api/admin/analytic/dataset/load` | загрузить dataset в analytic session |
| POST | `/api/admin/analytic/dataset/unload` | выгрузить dataset из analytic session |
| POST | `/api/admin/analytic/dataset/status` | статус analytic dataset session |
| POST | `/api/admin/analytic/anomaly/dbscan` | DBSCAN anomaly run |
| POST | `/api/admin/analytic/anomaly/isolation-forest` | Isolation Forest run |
| POST | `/api/admin/analytic/dataset/distribution` | distribution/stat overview |
| POST | `/api/admin/analytic/dataset/quality-check` | quality audit dataset |
| POST | `/api/admin/analytic/dataset/load-ohlcv` | repair missing OHLCV через analytic orchestrator; body проксируется как есть, для non-Bybit repair передавай `exchange` |
| POST | `/api/admin/analytic/dataset/recompute-features` | recompute features для exchange-aware таблицы; для non-Bybit передавай `exchange` |

#### Analytics / ML

| Method | Route | Downstream intent |
| ------ | ----- | ----------------- |
| POST | `/api/admin/analytics/train/start` | старт обучения |
| POST | `/api/admin/analytics/train/status` | статус обучения |
| POST | `/api/admin/analytics/model/list` | список моделей |
| POST | `/api/admin/analytics/model/load` | загрузка модели |
| POST | `/api/admin/analytics/predict` | prediction request |

### Admin facade: frontend/admin notes

- gateway не меняет shape успешного payload-а, поэтому frontend/admin code должен опираться на owner-docs сервисов `microservice_data` и `microservice_analitic`;
- gateway-level часть контракта для `/api/admin/*` — это auth, HTTP status, correlation, timeout/unavailable semantics и точный route→Kafka mapping;
- для quality-repair flow progress по-прежнему не идёт в ответе этого endpoint: он публикуется отдельно через SSE/admin event stream.

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

## POST /api/account/{register,login,refresh,logout}

### Account Auth Proxy: назначение

Gateway публикует тот же auth-flow, что и `microservice_account`, но под своим public/mobile base URL.

Это позволяет Flutter/Web клиенту работать только с gateway host и не держать отдельный account base URL.

### Account Auth Proxy: routes

| Method | Path | Auth | Назначение |
| ------ | ---- | ---- | ---------- |
| POST | `/api/account/register` | None | создать user-account |
| POST | `/api/account/login` | None | login по `email` или `login` |
| POST | `/api/account/refresh` | None | обновить access token по refresh token |
| POST | `/api/account/logout` | Bearer JWT | отозвать refresh token и завершить сессию |

### Account Auth Proxy: request notes

- `register` принимает `{ email, username, password }`;
- `login` принимает либо `{ email, password }`, либо `{ login, password }`;
- `login` также пропускает optional `deviceId` и `deviceName`;
- `refresh` и `logout` принимают `{ refreshToken }`.

Пример login payload по username:

```json
{
  "login": "admin",
  "password": "admin",
  "deviceName": "flutter-web"
}
```

### Account Auth Proxy: success response example

```json
{
  "accessToken": "...",
  "refreshToken": "...",
  "accessTokenExpiresAt": "2026-05-23T12:15:00Z",
  "refreshTokenExpiresAt": "2026-06-22T12:00:00Z",
  "uid": "9ab711df-2390-4364-841b-3269dc8f2c2a",
  "id": "9ab711df-2390-4364-841b-3269dc8f2c2a",
  "email": "user@example.com",
  "accountType": "user",
  "roles": ["user"],
  "user": {
    "id": "9ab711df-2390-4364-841b-3269dc8f2c2a",
    "email": "user@example.com",
    "username": "trader01",
    "status": "active",
    "roles": ["user"],
    "createdAt": "2026-05-01T12:00:00Z",
    "updatedAt": "2026-05-01T12:00:00Z"
  }
}
```

### Account Auth Proxy: error semantics

- успешные auth proxy responses gateway по-прежнему возвращает как downstream JSON/body без переупаковки;
- если downstream auth-flow вернул non-success status, gateway нормализует ответ в `ErrorResponse` с `code = "account_proxy_error"`, сохраняя HTTP status code и вытаскивая `detail/message/error` из downstream body, если это возможно;
- если proxy path сам не может достучаться до account-service, gateway возвращает structured `503 Service Unavailable`;
- успешный `logout` через gateway возвращает `204 No Content`.

Практическое правило для клиента:

- `code = "account_proxy_error"` означает, что upstream auth route ответил non-2xx и gateway перепаковал его в единый envelope;
- `code = "account_profile_unavailable"` относится только к `GET /api/account/me` и означает сбой Kafka-backed profile lookup;
- успешные auth responses по-прежнему не получают gateway wrapper и должны парситься по downstream account contract.

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

```json
{
  "status": 503,
  "title": "Service Unavailable",
  "code": "account_profile_unavailable",
  "detail": "Account service timeout",
  "correlationId": "2de4b66e-8b79-4a3e-b3a1-7e5d65f89e74",
  "timestamp": "2026-05-24T05:20:00Z"
}
```

### Account Me: frontend behavior

- использовать для отдельного профиля/кабинета;
- для стартового экрана лучше сначала опираться на `/api/app/bootstrap`;
- `503 account_profile_unavailable` обрабатывать как временную недоступность account downstream, но без отдельного legacy-parser branch.

---

## GET /api/dashboard

### Dashboard: назначение

Возвращает агрегированный main-screen payload.

### Dashboard: how it works

Gateway параллельно запрашивает:

- `portfolio` summary для аутентифицированного user;
- `marketOverview`;
- `trendingAssets`;
- `latestNews`.

Отказ одной секции не валит весь ответ. Endpoint возвращает `200`, а деградация кодируется в `meta.degradedSections`.

Guest-mode важен отдельно:

- если токена нет, gateway не пытается собирать `portfolio` вообще;
- `portfolio` в ответе остаётся `null`, но секция не помечается degraded, потому что это ожидаемое guest-поведение, а не downstream failure.

### Dashboard: request

```http
GET /api/dashboard
```

`Authorization: Bearer <access-token>` опционален. С токеном endpoint работает как `user`-dashboard, без токена — как `guest`-dashboard.

### Dashboard: response example

```json
{
  "portfolio": null,
  "marketOverview": null,
  "trendingAssets": [],
  "latestNews": [],
  "meta": {
    "degradedSections": ["market", "news"],
    "generatedAt": "2026-05-15T18:22:00Z"
  }
}
```

### Dashboard: field reference

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `portfolio` | object/null | для guest всегда `null`; для user может быть `null`, если personal downstream деградировал |
| `marketOverview` | object/null | агрегированный обзор рынка |
| `trendingAssets` | array | список трендовых активов |
| `latestNews` | array | краткие карточки новостей |
| `meta.degradedSections` | string[] | секции, которые не удалось собрать полноценно |
| `meta.generatedAt` | string | время сборки ответа |

### Dashboard: current implementation note

Текущая реализация уже не падает в degraded по умолчанию на каждой секции:

- `marketOverview` и `trendingAssets` получают snapshot-derived payload из gateway market snapshot layer;
- `latestNews` использует тот же sorted fallback path, что и `/api/news` / `/api/news/home`;
- `portfolio` для аутентифицированного пользователя берётся из gateway-owned frontend state и отражает только linked exchanges, созданные через `/api/exchanges/*`.

Из-за этого `dashboard` чаще возвращает полноценный `200` без degraded sections, но personal/settings часть по-прежнему зависит от gateway-owned cache-backed state, а не от отдельного owner-service.

### Dashboard: errors

- отсутствие токена не считается ошибкой: это guest-mode;
- `500` только при внутренней необработанной ошибке gateway.

---

## GET /api/v1/market/overview

### Market Overview: назначение

Публичный home-screen endpoint для краткого market summary без персональных данных.

### Market Overview: how it works

Gateway читает cached snapshot из `IMarketServiceClient`, который поверх Bybit linear tickers собирает list-ready universe и из него выводит home metrics.

Текущая семантика intentionally pragmatic:

- `totalMarketCap` = proxy-агрегат по snapshot universe, где per-symbol `marketCap` выводится из `openInterestValue`, а при его отсутствии — из `turnover24h`;
- `btcDominance` считается как доля `BTCUSDT` внутри того же proxy market-cap universe;
- `fearGreedValue` / `fearGreedLabel` — gateway-level heuristic по breadth и average 24h change, а не внешний индекс;
- `trendingAssets` ранжируются по gateway-derived `TrendingScore`, который учитывает magnitude 24h change и liquidity proxy.

### Market Overview: request

```http
GET /api/v1/market/overview
```

Авторизация не требуется.

### Market Overview: response example

```json
{
  "marketOverview": {
    "totalMarketCap": 1410000000,
    "btcDominance": 58.164729,
    "volume24h": 2345000000,
    "activeAssets": 3,
    "fearGreedValue": 61,
    "fearGreedLabel": "Greed"
  },
  "trendingAssets": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  "meta": {
    "generatedAt": "2026-05-24T03:45:30Z",
    "updatedAt": "2026-05-24T03:45:00Z",
    "degradedSections": [],
    "degradedFields": []
  }
}
```

### Market Overview: frontend behavior

- считать этот endpoint public source of truth для home screen overview;
- воспринимать market numbers как snapshot-derived proxies, а не как canonical multi-exchange market-cap feed;
- использовать `meta.updatedAt` как timestamp последнего snapshot refresh, а `meta.generatedAt` как время ответа gateway;
- `meta.degradedFields` и `meta.degradedSections` использовать как warning, а не как hard-fail trigger;
- frontend cache должен учитывать route-level freshness policy `max-age=30, stale-while-revalidate=120`.

---

## GET /api/v1/market/tickers

### Market Tickers: назначение

List-screen endpoint для market watch / asset directory / search results.

### Market Tickers: request

```http
GET /api/v1/market/tickers?page=1&pageSize=25&search=btc&sortBy=change24h&sortDir=desc&symbols=BTCUSDT,ETHUSDT
```

Авторизация не требуется.

### Market Tickers: query parameters

| Параметр | Тип | Default | Правило |
| -------- | --- | ------- | ------- |
| `page` | number | `1` | minimum `1` |
| `pageSize` | number | `25` | clamp `1..100` |
| `search` | string | `null` | match по `symbol`, `displayName`, `baseAsset`, `quoteAsset` |
| `sortBy` | string | `rank` | one of `symbol`, `displayName`, `price`, `change24h`, `volume24h`, `marketCap`, `high24h`, `low24h`, `rank`, `updatedAt` |
| `sortDir` | string | `desc` | `asc` or `desc` |
| `symbols` | string | `null` | comma-separated whitelist of symbols |

### Market Tickers: response example

```json
{
  "items": [
    {
      "symbol": "BTCUSDT",
      "displayName": "BTC / USDT",
      "baseAsset": "BTC",
      "quoteAsset": "USDT",
      "price": 106500,
      "change24h": 2.5,
      "volume24h": 1250000000,
      "marketCap": 820000000,
      "high24h": 107200,
      "low24h": 103800,
      "rank": 1,
      "logoUrl": "https://cdn.test/btc.svg",
      "exchangeCount": 1,
      "updatedAt": "2026-05-24T03:45:00Z",
      "isTrending": true
    }
  ],
  "total": 1,
  "page": 1,
  "pageSize": 25,
  "search": "btc",
  "sortBy": "change24h",
  "sortDir": "desc",
  "meta": {
    "generatedAt": "2026-05-24T03:45:30Z",
    "updatedAt": "2026-05-24T03:45:00Z",
    "degradedSections": [],
    "degradedFields": []
  }
}
```

### Market Tickers: frontend behavior

- использовать для list/table/grid screens вместо многократных `chart` запросов;
- `marketCap` остаётся proxy-derived полем и может быть `null`, если snapshot не смог вычислить proxy;
- `meta.degradedFields` показывает, какие числовые поля snapshot считает частично деградированными;
- route рассчитан на короткий public cache: `max-age=15, stale-while-revalidate=45`.

---

## POST /api/v1/market/quotes/batch

### Batch Quotes: назначение

Дешёвый refresh path для небольшого symbol set без pagination/search metadata.

### Batch Quotes: request

```http
POST /api/v1/market/quotes/batch
Content-Type: application/json

{
  "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
}
```

### Batch Quotes: response example

```json
{
  "items": [
    {
      "symbol": "BTCUSDT",
      "price": 106500,
      "change24h": 2.5,
      "high24h": 107200,
      "low24h": 103800,
      "volume24h": 1250000000,
      "updatedAt": "2026-05-24T03:45:00Z"
    }
  ],
  "missingSymbols": ["DOGEUSDT"],
  "meta": {
    "generatedAt": "2026-05-24T03:45:30Z",
    "updatedAt": "2026-05-24T03:45:00Z",
    "degradedSections": [],
    "degradedFields": []
  }
}
```

### Batch Quotes: frontend behavior

- `missingSymbols` не считается hard-error: это signal, что symbol отсутствует в текущем snapshot universe;
- endpoint не требует page/sort/search и подходит для widget-level polling;
- route рассчитан на самый короткий public cache среди market snapshot endpoints: `max-age=10, stale-while-revalidate=20`.

---

## GET /api/v1/market/converter/quote

### Converter Quote: назначение

Возвращает lightweight conversion quote между двумя asset-ами через snapshot USD prices.

### Converter Quote: request

```http
GET /api/v1/market/converter/quote?fromAsset=BTC&toAsset=USDT&amount=1
```

### Converter Quote: response example

```json
{
  "fromAsset": "BTC",
  "toAsset": "USDT",
  "amount": 1,
  "rate": 106500,
  "convertedAmount": 106500,
  "source": "bybit-linear-tickers",
  "generatedAt": "2026-05-24T03:45:30Z",
  "updatedAt": "2026-05-24T03:45:00Z"
}
```

### Converter Quote: error cases

- `400` если `fromAsset` / `toAsset` пустые;
- `400` если `amount <= 0`;
- `400` если gateway snapshot не знает один из asset-ов и не может вывести USD cross-rate.

### Converter Quote: frontend behavior

- `source` сейчас отражает production snapshot source и в live path равен `bybit-linear-tickers`;
- route использует тот же короткий freshness window, что и batch quotes: `max-age=10, stale-while-revalidate=20`.

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
| `meta.coverage` | string | `full`, `partial`, `pending` |
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

## Gateway-local frontend contract routes

### GET /api/portfolio/summary

Возвращает расширенную сводку портфеля для authenticated user.

Текущая реализация строится полностью внутри gateway по linked exchanges, сохранённым в `IFrontendContractState`:

- `totalValue` = сумма `cachedBalance` по linked exchanges;
- `byAsset` пока пустой массив;
- `byExchange` содержит по одной записи на linked exchange;
- `isSynced` сейчас `false`, `change24h` пока `0`.

Если gateway настроен с Redis, эти данные переживают restart и становятся видны другим instances. Без Redis behaviour деградирует в прежний memory-only fallback.

### /api/exchanges/*

| Method | Path | Поведение |
| ------ | ---- | --------- |
| GET | `/api/exchanges/available` | возвращает каталог `binance` / `bybit` / `kraken` с `isConnected` для текущего user |
| GET | `/api/exchanges/linked` | возвращает linked exchanges текущего user |
| POST | `/api/exchanges/link` | создаёт/перезаписывает linked exchange по `slug` |
| PATCH | `/api/exchanges/link/{slug}` | обновляет `maskedKey` и/или `isActive` |
| DELETE | `/api/exchanges/link/{slug}` | удаляет linked exchange, успешный ответ = `204 No Content` |

Важные contract details:

- request на создание принимает `{ slug, apiKey, apiSecret? }`;
- ответ никогда не возвращает raw keys, только `maskedKey`;
- `cachedBalance` сейчас gateway-local placeholder и по умолчанию `0`.

### /api/alerts/*

| Method | Path | Поведение |
| ------ | ---- | --------- |
| GET | `/api/alerts` | список алертов текущего user |
| POST | `/api/alerts` | создаёт алерт |
| PATCH | `/api/alerts/{id}` | частично обновляет алерт |
| DELETE | `/api/alerts/{id}` | удаляет алерт, успешный ответ = `204 No Content` |

Request shape для create:

```json
{
  "symbol": "BTCUSDT",
  "condition": "above",
  "targetPrice": 70000,
  "isEnabled": true
}
```

Runtime normalization:

- `symbol` нормализуется в uppercase;
- `condition` нормализуется в lowercase;
- `id` генерируется gateway как строковый GUID без разделителей.

### /api/services/toggles

- `GET /api/services/toggles` возвращает `{ news, alerts, portfolioSync, marketOverview }`;
- `PATCH /api/services/toggles` принимает partial body с любым подмножеством этих полей и возвращает обновлённый объект.

Исходный state bootstrap-ится из `FeatureFlagsSettings`, дальше сохраняется через `IDistributedCache`: при Redis-конфигурации toggles shared/durable, без Redis остаются memory-only внутри текущего процесса.

### GET /api/admin/{summary,users,services,statistics}

Это lightweight mobile-admin surface под bearer JWT с ролью `admin`, отдельный от server-to-server facade `POST /api/admin/*`.

Текущие ограничения:

- `GET /api/admin/users` сейчас возвращает snapshot только вызывающего admin-user, а не полный user directory;
- `GET /api/admin/services` строится из текущих service toggles;
- `summary` и `statistics` считают linked exchanges/alerts/users только внутри gateway-owned cache-backed state, а не из отдельного admin owner-service.

Использовать эти routes как mobile-facing contract можно уже сейчас, но не как cross-instance persistent admin truth.

---

## GET /api/news

### News: назначение

Возвращает список новостей для публичной ленты.

### News: how it works

1. Принимает `limit`.
2. Clamp-ит его в диапазон `1..100`.
3. Запрашивает news client.
4. Сортирует items по `publishedAt desc` и режет до итогового `limit`.
5. Даже при недоступности downstream отвечает `200`, но ставит `degraded = true`.

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
  "degraded": false
}
```

### News: field reference

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `items` | array | новости |
| `total` | number | число элементов в `items` |
| `degraded` | boolean | downstream news currently unavailable |

#### News item fields

| Поле | Тип | Смысл |
| ---- | --- | ----- |
| `id` | string | идентификатор новости |
| `title` | string | заголовок карточки |
| `summary` | string | краткое описание |
| `source` | string | источник публикации |
| `url` | string/null | canonical article URL |
| `imageUrl` | string/null | preview image URL |
| `publishedAt` | string | время публикации |
| `tags` | string[] | произвольные теги |

### News: current implementation note

`GET /api/news` и `GET /api/news/home` используют один и тот же sorted response builder. `GET /api/news/home` — это просто compact variant с фиксированным `limit = 3` для home screen. Каждый `NewsItemDto` может дополнительно нести `url`, `imageUrl` и `tags`. Пустая лента остаётся нормальным `200`-сценарием, а `degraded = true` выставляется только при реальной ошибке news client.

Frontend cache note:

- обе news routes отдают `Cache-Control: public, max-age=30, stale-while-revalidate=300`.

---

## GET /api/news/home

### News Home: назначение

Compact home-screen variant news feed.

### News Home: request

```http
GET /api/news/home
```

Авторизация не требуется. Endpoint всегда использует `limit = 3` и тот же response shape, что и `GET /api/news`.

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
  "degraded": false
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

Текущий fallback path возвращает успешный пустой inbox для текущего user, поэтому обычный сценарий сейчас — `items = []`, `unreadCount = 0`, `degraded = false`. `degraded = true` остаётся только для реальной ошибки notifications client.

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
