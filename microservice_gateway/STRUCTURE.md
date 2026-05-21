# microservice_gateway — Структура

> Обновляй этот файл при изменении BFF-маршрутов, Kafka-клиентов, агрегаторов или состава тестов.

---

## Связанная документация

- [README.md](README.md) — runbook, endpoint-ы и текущая архитектура gateway
- [API.md](API.md) — frontend-oriented HTTP reference с примерами входа/выхода и правилами интеграции
- [../docs/agents/services/microservice_gateway.md](../docs/agents/services/microservice_gateway.md) — агентный профиль сервиса
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first workflow

---

## Корень сервиса

| Файл | Описание |
| ---- | -------- |
| `GatewayService.sln` | Solution-файл .NET |
| `Dockerfile` | Контейнеризация gateway |
| `docker-compose.yml` | Локальный compose-стек gateway; container healthcheck смотрит `GET /health/ready`, а не liveness-only `/health` |
| `global.json` | Привязка .NET SDK |
| `README.md` | Основная документация сервиса |
| `API.md` | Подробная HTTP-спецификация для frontend: headers, auth, вход, выход, ошибки, degraded/pending semantics |
| `STRUCTURE.md` | Этот файл |

---

## src/GatewayService.API/

| Папка / файл | Назначение |
| ------------ | ---------- |
| `Program.cs` | bootstrap приложения, DI, middleware pipeline, browser-facing CORS policy (`UseCors`) и health routing (`/health` liveness, `/health/ready` readiness). Readiness теперь требует и Kafka bootstrap, и живой reply-inbox path через `KafkaRequestReplyHealthCheck` |
| `Aggregators/` | BFF-оркестрация составных экранов и bootstrap-ответов; `DashboardAggregator` guest-aware и не пытается собирать `portfolio`, если запрос пришёл без user identity |
| `Clients/` | Downstream clients, включая Kafka request/reply к account service |
| `Controllers/` | HTTP endpoints gateway; `DashboardController` работает в optional-auth режиме (guest получает public dashboard, user — personal sections), а `AdminController` помечен `DisableCors`, потому что `/api/admin/*` рассчитан на server-to-server admin facade, а не на browser JS |
| `DTOs/` | Контракты ответов и ошибок; `ErrorResponse` включает optional `code` для машинно-читаемой диагностики |
| `Extensions/` | Регистрация сервисов и инфраструктурных зависимостей; `ServiceCollectionExtensions` теперь поднимает browser CORS policy из `CorsSettings` (`AllowAnyOrigin` или explicit `AllowedOrigins`) и включает preflight cache TTL |
| `Filters/` | Action filters; `AdminApiKeyFilter` — проверка shared-token для admin facade, различает `admin_token_missing` и `admin_token_invalid` |
| `Kafka/` | Kafka settings, topics, `IKafkaRequestClient`, `IKafkaRequestClientProbe`, `KafkaBrokerHealthCheck`, `KafkaRequestReplyHealthCheck` и request client; `AdminTopics` — topic-константы admin facade. `KafkaRequestClient` bootstrap-ит per-instance reply-inbox `reply.gateway.{instanceId}` в background-loop и теперь помечает reply path ready только после реального consumer assignment. Если Kafka Admin create не подтверждается в startup budget, клиент делает fallback bootstrap publish в сам reply topic и продолжает retry-loop до тех пор, пока inbox не будет существовать и назначен consumer-у; это устраняет ложный symmetric `504` по Kafka-backed admin route-ам после старта gateway раньше Redpanda/controller. `KafkaBrokerHealthCheck` проверяет bootstrap listener, а `KafkaRequestReplyHealthCheck` — assignment reply inbox для `/health/ready` и compose healthcheck. Runtime diagnostics логируют `KafkaRequest start/produced/success/timeout/failed` с topic, replyInbox, duration и correlationId без payload. |
| `Market/` | Полный market API — см. ниже |
| `Middleware/` | CorrelationId, exception handling и другие cross-cutting middleware |
| `Settings/` | strongly-typed конфиги; `AdminSettings` — конфиг admin facade (SharedToken, таймауты), `CorsSettings` — browser-facing CORS policy (`AllowAnyOrigin`, `AllowedOrigins`, `PreflightMaxAgeSeconds`) |
| `Common/` | общие типы и вспомогательные abstractions |
| `appsettings*.json` | конфигурация окружений |

### Market/

| Файл | Назначение |
| ---- | ---------- |
| `IChartService.cs` | интерфейс chart-сервиса |
| `ChartService.cs` | ядро: кэш → coverage → sync lazy ingest missing window → rows; при первом запросе по валидному symbol/timeframe пытается синхронно догрузить нужный диапазон и только потом вернуть candles. Lazy hydrate не шлёт прямой `cmd.data.dataset.ingest`, а создаёт queued ingest-job через `DataServiceClient`, поэтому попадает в data-service ingest queue (cap 4, per-table serialization). `pending`/`partial` остаются fallback-состояниями при занятом ingest-lock, timeout/error ingest или `claim_check`; ingest-lock при ошибке переводится в cooldown. |
| `ChartRequestQueue.cs` | coalescing-декоратор: идентичные `(symbol, timeframe, limit)` запросы разделяют один downstream-вызов; каждый caller имеет независимый `CancellationToken` |
| `IMarketCacheService.cs` / `MarketCacheService.cs` | Redis-кэш с stampede protection (`SetIfNotExistsAsync`) |
| `IMarketConfigService.cs` / `MarketConfigService.cs` | конфиг символов и таймфреймов |
| `IBybitSymbolProvider.cs` / `BybitSymbolProvider.cs` | получение активных символов с Bybit |
| `IDataServiceClient.cs` / `DataServiceClient.cs` | Kafka-клиент к data-сервису; различает inline rows и `claim_check`, а lazy hydrate выполняет через `cmd.data.dataset.jobs.start` + polling `cmd.data.dataset.jobs.get` до terminal status. Это направляет chart-triggered ingest в тот же `DatasetJobRunner`, что и admin queue, с общим cap `4` и per-table lock по `target_table`. |
| `MarketSettings.cs` | strongly-typed конфиг market-блока (включает 4 queue-поля) |
| `TimeframeMap.cs` | маппинг таймфреймов ID → Bybit interval |
| `CandleCountGrid.cs` | валидация и маппинг количества свечей |
| `DataTopics.cs` | Kafka topic-константы для chart-path: coverage/rows и dataset job control (`jobs.start`, `jobs.get`) |

---

## ../deploy/

| Файл | Назначение |
| ---- | ---------- |
| `modelline-deploy.yml` | Root-level deployment config, который теперь указывает реальные compose service names для infra/gateway/data/analytic/account; gateway deployable unit = `gateway-service`, analytic = `api`, account = `account-api` |
| `reconcile.ps1` | Windows reconcile script для root-level deploy конфигурации |
| `reconcile.sh` | Linux/macOS reconcile script для root-level deploy конфигурации; parser исправлен, чтобы multi-service YAML не падал на втором entry под `set -e` |
| `print_token.sh` | Shell helper: без аргументов печатает backend token `ADMIN_SHARED_TOKEN` из `microservice_gateway/.env` |
| `set_token.sh` | Shell helper: принимает ровно один позиционный аргумент `./set_token.sh <big-token>` и пишет его в `microservice_admin/.env` как `ADMIN_BACKEND_SHARED_TOKEN` |
| `status.ps1` | статус контейнеров по root-level deploy конфигурации |

---

## tests/

| Папка | Назначение |
| ----- | ---------- |
| `GatewayService.UnitTests/` | юнит-тесты агрегаторов, клиентов и middleware; `DashboardAggregatorTests` дополнительно фиксирует guest-path без `portfolio` downstream call |
| `GatewayService.IntegrationTests/` | интеграционные тесты in-process; `GatewayIntegrationTests` теперь фиксирует и anonymous `GET /api/dashboard` (guest payload без degraded `portfolio`), и browser CORS на `GET /api/news` / preflight `OPTIONS /api/dashboard` |
| `GatewayService.ContractTests/` | контрактные тесты HTTP API |
| `GatewayService.SmokeTests/` | smoke-проверки ключевых сценариев gateway |

---

## Что считать изменением структуры

- новые BFF endpoints или downstream integrations
- изменение состава папок `Aggregators`, `Clients`, `Kafka`, `Middleware`
- изменение тестового контура или типов тестов
- изменение схемы конфигурации и обязательных env/appsettings
