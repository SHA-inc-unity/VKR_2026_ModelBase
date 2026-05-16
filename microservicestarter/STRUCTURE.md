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
| ---- | -------- |
| `services.conf` | Реестр сервисов, который читают все launcher-скрипты |
| `start.sh` / `start.ps1` | Запуск всех или выбранных сервисов. Поддерживают `core`, `noadmin`, `onlyadmin`, `full`, `scheduler`, `build`, `logs`. Для multi-service запуска сначала поднимают `microservice_infra`, затем fan-out запускают остальные сервисы параллельно. Linux-версия заранее создаёт repo-local bind-mount каталоги в `.runtime-data/` и нормализует права записи перед `docker compose up`. Для split deployment режим `onlyadmin` теперь только поднимает `admin-online` без принудительного `--build`; rebuild остаётся в `restart.*`. Дополнительно `onlyadmin` принимает backend host/IP аргументом или, если он не задан, заранее запрашивает его в консоли и записывает derived `ONLINE_*` в `microservice_admin/.env` ещё до запуска `docker compose`. |
| `stop.sh` / `stop.ps1` | Остановка сервисов, clean/prune режимы. `clean` для stateful сервисов удаляет не только Docker volumes, но и repo-local runtime-каталоги в `../.runtime-data/` (`microservice_infra`, `microservice_account`, `microservice_data`, `microservice_analitic`) |
| `restart.sh` / `restart.ps1` | `git pull` + пересборка + перезапуск. Поддерживают `core`, `noadmin`, `onlyadmin`, `full`, `deps`, `api`. Для multi-service режима сначала синхронно обновляют/поднимают `microservice_infra`, потом перезапускают остальные сервисы параллельно; `git pull` выполняется один раз до fan-out. Linux-версия заранее создаёт repo-local bind-mount каталоги в `.runtime-data/` и нормализует права записи перед `docker compose up`. В `onlyadmin` могут принять backend host/IP аргументом или, если он не задан, заранее спросить его в консоли и перезаписать derived `ONLINE_*` в `microservice_admin/.env` перед rebuild+up. |
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
- изменение того, где хранятся repo-local runtime-данные сервисов и как `clean` их удаляет
- изменение того, какие host-порты публикуются обычным `start` и split-режимами (`local/full` — `8501` от infra-nginx; `onlyadmin` — `8501` от `admin-online` на отдельном хосте)
