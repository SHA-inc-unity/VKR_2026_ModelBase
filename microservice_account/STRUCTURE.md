# microservice_account — Структура

> Обновляй этот файл при изменении модулей, API-контрактов, Kafka-интеграции или состава тестов.

---

## Связанная документация

- [README.md](README.md) — runbook, API и Kafka-контракты сервиса
- [../docs/agents/services/microservice_account.md](../docs/agents/services/microservice_account.md) — агентный профиль сервиса
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first workflow

---

## Корень сервиса

| Файл | Описание |
| ---- | -------- |
| `AccountService.sln` | Solution-файл .NET |
| `Dockerfile` | Контейнеризация API-сервиса |
| `docker-compose.yml` | Локальный стек сервиса: API, PostgreSQL, Redis profile. PostgreSQL хранит данные в repo-local bind mount `../.runtime-data/microservice_account/postgres`, а Redis profile — в `../.runtime-data/microservice_account/redis` |
| `global.json` | Привязка .NET SDK |
| `README.md` | Описание сервиса, HTTP и Kafka-контракты |
| `STRUCTURE.md` | Этот файл |

---

## src/

### `AccountService.Domain/`

Чистая доменная модель без инфраструктурных зависимостей.

- `Entities/` — пользователь, роль (`guest` / `user` / `admin`), refresh-token, audit-сущности, настройки
- `Enums/` — доменные перечисления

### `AccountService.Application/`

Слой use-case и прикладных сервисов.

- `Services/` — account flow, password hashing, JWT/token management; `AccountAppService` возвращает UID/accountType/roles в auth response, публично регистрирует только роль `user` и принимает login по email или username
- `Interfaces/` — контракты репозиториев, сервисов и кеша
- `DTOs/` — request/response модели
- `Validators/` — FluentValidation-валидаторы
- `Common/` — общие типы результата и ошибок

### `AccountService.Infrastructure/`

Интеграция с внешними зависимостями.

- `Data/` — `AccountDbContext`, EF Core configurations
- `Repositories/` — реализации репозиториев
- `Cache/` — Redis/no-op реализации token cache
- `Migrations/` — EF Core миграции

### `AccountService.API/`

HTTP и Kafka-входные точки.

- `Program.cs` — bootstrap, DI, middleware pipeline
- `Controllers/` — public и internal HTTP endpoints
- `Extensions/` — регистрация сервисов и миграций; startup migration flow может создать/promote login-only admin account через `AdminBootstrap:*`, а при полностью пустом bootstrap-конфиге поднимает дефолтного admin-пользователя `admin/admin`
- `Kafka/` — Kafka request/reply интеграция сервиса
- `Middleware/` — global exception handling и cross-cutting concerns
- `appsettings*.json` — конфигурация окружений

---

## tests/

| Папка | Назначение |
| ----- | ---------- |
| `AccountService.UnitTests/` | Юнит-тесты прикладной логики |
| `AccountService.IntegrationTests/` | Интеграционные тесты с реальной инфраструктурой |
| `AccountService.ContractTests/` | Контрактные тесты публичного API |

---

## Что считать изменением структуры

- новые контроллеры, Kafka handlers или contracts
- изменение слоёв Clean Architecture и их ответственности
- изменение набора тестовых проектов
- изменение обязательных конфигурационных файлов
