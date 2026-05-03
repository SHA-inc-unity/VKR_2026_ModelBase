# microservicestarter — Структура

> Обновляй этот файл при изменении launcher-скриптов, режимов запуска или реестра сервисов.

---

## Связанная документация

- [README.md](README.md) — быстрый старт и операционные сценарии launcher-а
- [../docs/agents/services/microservicestarter.md](../docs/agents/services/microservicestarter.md) — агентный профиль каталога
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first workflow

---

## Корень каталога

| Файл | Описание |
|------|----------|
| `services.conf` | Реестр сервисов, который читают все launcher-скрипты |
| `start.sh` / `start.ps1` | Запуск всех или выбранных сервисов |
| `stop.sh` / `stop.ps1` | Остановка сервисов, clean/prune режимы |
| `restart.sh` / `restart.ps1` | `git pull` + пересборка + перезапуск |
| `update.sh` / `update.ps1` | Только `git pull`, без рестарта контейнеров |
| `status.sh` / `status.ps1` | Сводка по состоянию compose-стеков |
| `README.md` | Описание launcher-а и режимов запуска |
| `STRUCTURE.md` | Этот файл |

---

## services.conf

Текущий реестр сервисов launcher-а:

- `microservice_infra`
- `microservice_analitic`
- `microservice_account`
- `microservice_gateway`
- `microservice_data`
- `microservice_admin`

Каждая строка имеет формат `<service_name>  <path_relative_to_repo_root>`.

---

## Что считать изменением структуры

- добавление, удаление или переименование сервисов в `services.conf`
- изменение поддерживаемых режимов `start/stop/restart/update/status`
- изменение аргументов PowerShell или shell-версий скриптов
- изменение договорённостей по `.env`, Docker Compose и lifecycle launcher-а