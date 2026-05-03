# microservice_gateway — Структура

> Обновляй этот файл при изменении BFF-маршрутов, Kafka-клиентов, агрегаторов или состава тестов.

---

## Связанная документация

- [README.md](README.md) — runbook, endpoint-ы и текущая архитектура gateway
- [../docs/agents/services/microservice_gateway.md](../docs/agents/services/microservice_gateway.md) — агентный профиль сервиса
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first workflow

---

## Корень сервиса

| Файл | Описание |
|------|----------|
| `GatewayService.sln` | Solution-файл .NET |
| `Dockerfile` | Контейнеризация gateway |
| `docker-compose.yml` | Локальный compose-стек gateway |
| `global.json` | Привязка .NET SDK |
| `README.md` | Основная документация сервиса |
| `STRUCTURE.md` | Этот файл |

---

## src/GatewayService.API/

| Папка / файл | Назначение |
|---|---|
| `Program.cs` | bootstrap приложения, DI, middleware pipeline |
| `Aggregators/` | BFF-оркестрация составных экранов и bootstrap-ответов |
| `Clients/` | Downstream clients, включая Kafka request/reply к account service |
| `Controllers/` | HTTP endpoints gateway |
| `DTOs/` | Контракты ответов и ошибок |
| `Extensions/` | Регистрация сервисов и инфраструктурных зависимостей |
| `Kafka/` | Kafka settings, topics и request client |
| `Middleware/` | CorrelationId, exception handling и другие cross-cutting middleware |
| `Settings/` | strongly-typed конфиги |
| `Common/` | общие типы и вспомогательные abstractions |
| `appsettings*.json` | конфигурация окружений |

---

## tests/

| Папка | Назначение |
|------|------------|
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