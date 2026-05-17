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
| `docker-compose.yml` | Локальный compose-стек gateway |
| `global.json` | Привязка .NET SDK |
| `README.md` | Основная документация сервиса |
| `API.md` | Подробная HTTP-спецификация для frontend: headers, auth, вход, выход, ошибки, degraded/pending semantics |
| `STRUCTURE.md` | Этот файл |

---

## src/GatewayService.API/

| Папка / файл | Назначение |
| ------------ | ---------- |
| `Program.cs` | bootstrap приложения, DI, middleware pipeline |
| `Aggregators/` | BFF-оркестрация составных экранов и bootstrap-ответов |
| `Clients/` | Downstream clients, включая Kafka request/reply к account service |
| `Controllers/` | HTTP endpoints gateway |
| `DTOs/` | Контракты ответов и ошибок |
| `Extensions/` | Регистрация сервисов и инфраструктурных зависимостей |
| `Filters/` | Action filters; `AdminApiKeyFilter` — проверка shared-token для admin facade |
| `Kafka/` | Kafka settings, topics и request client; `AdminTopics` — topic-константы admin facade |
| `Market/` | Полный market API — см. ниже |
| `Middleware/` | CorrelationId, exception handling и другие cross-cutting middleware |
| `Settings/` | strongly-typed конфиги; `AdminSettings` — конфиг admin facade (SharedToken, таймауты) |
| `Common/` | общие типы и вспомогательные abstractions |
| `appsettings*.json` | конфигурация окружений |

### Market/

| Файл | Назначение |
| ---- | ---------- |
| `IChartService.cs` | интерфейс chart-сервиса |
| `ChartService.cs` | ядро: кэш → coverage → ingest-lock → rows; ingest lock снимается и при ошибке (cooldown); window-scoped coverage |
| `ChartRequestQueue.cs` | coalescing-декоратор: идентичные `(symbol, timeframe, limit)` запросы разделяют один downstream-вызов; каждый caller имеет независимый `CancellationToken` |
| `IMarketCacheService.cs` / `MarketCacheService.cs` | Redis-кэш с stampede protection (`SetIfNotExistsAsync`) |
| `IMarketConfigService.cs` / `MarketConfigService.cs` | конфиг символов и таймфреймов |
| `IBybitSymbolProvider.cs` / `BybitSymbolProvider.cs` | получение активных символов с Bybit |
| `IDataServiceClient.cs` / `DataServiceClient.cs` | Kafka-клиент к data-сервису; различает inline rows и `claim_check`, чтобы chart-path не путал large payload с пустым результатом |
| `MarketSettings.cs` | strongly-typed конфиг market-блока (включает 4 queue-поля) |
| `TimeframeMap.cs` | маппинг таймфреймов ID → Bybit interval |
| `CandleCountGrid.cs` | валидация и маппинг количества свечей |
| `DataTopics.cs` | Kafka topic-константы для ingest |

---

## ../deploy/

| Файл | Назначение |
| ---- | ---------- |
| `modelline-deploy.yml` | Root-level deployment config, который включает gateway как deployable unit |
| `reconcile.ps1` | Windows reconcile script для root-level deploy конфигурации |
| `reconcile.sh` | Linux/macOS reconcile script для root-level deploy конфигурации |
| `status.ps1` | статус контейнеров по root-level deploy конфигурации |

---

## tests/

| Папка | Назначение |
| ----- | ---------- |
| `GatewayService.UnitTests/` | юнит-тесты агрегаторов, клиентов и middleware |
| `GatewayService.IntegrationTests/` | интеграционные тесты in-process |
| `GatewayService.ContractTests/` | контрактные тесты HTTP API |
| `GatewayService.SmokeTests/` | smoke-проверки ключевых сценариев gateway |

---

## Что считать изменением структуры

- новые BFF endpoints или downstream integrations
- изменение состава папок `Aggregators`, `Clients`, `Kafka`, `Middleware`
- изменение тестового контура или типов тестов
- изменение схемы конфигурации и обязательных env/appsettings
