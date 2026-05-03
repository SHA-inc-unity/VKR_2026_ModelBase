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
| `start.sh` / `start.ps1` | Запуск всех или выбранных сервисов. Поддерживают `core`, `noadmin`, `onlyadmin`, `full`, `scheduler`, `build`, `logs`. Для multi-service запуска сначала поднимают `microservice_infra`, затем fan-out запускают остальные сервисы параллельно. |
| `stop.sh` / `stop.ps1` | Остановка сервисов, clean/prune режимы |
| `restart.sh` / `restart.ps1` | `git pull` + пересборка + перезапуск. Поддерживают `core`, `noadmin`, `onlyadmin`, `full`, `deps`, `api`. Для multi-service режима сначала синхронно обновляют/поднимают `microservice_infra`, потом перезапускают остальные сервисы параллельно; `git pull` выполняется один раз до fan-out. |
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
- изменение split-deployment режимов `noadmin` и `onlyadmin`
- изменение аргументов PowerShell или shell-версий скриптов
- изменение договорённостей по `.env`, Docker Compose и lifecycle launcher-а
- изменение того, какие host-порты публикуются обычным `start` и split-режимами (`local/full` — `8501` от infra-nginx; `onlyadmin` — `8501` от `admin-online` на отдельном хосте)
