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
| `start.sh` / `start.ps1` | Запуск всех или выбранных сервисов. Поддерживают `core`, `noadmin`, `onlyadmin`, `full`, `scheduler`, `build`, `logs`. Для multi-service запуска сначала поднимают `microservice_infra`, затем fan-out запускают остальные сервисы параллельно. Linux-версия заранее создаёт repo-local bind-mount каталоги в `.runtime-data/` и нормализует права записи перед `docker compose up`. Для split deployment режим `onlyadmin` теперь только поднимает `admin-online` без принудительного `--build`; rebuild остаётся в `restart.*`. Дополнительно `onlyadmin` принимает backend host/IP аргументом или, если он не задан, заранее запрашивает его в консоли, выводит derived `ONLINE_*`, предлагает `ADMIN_BACKEND_BASE_URL` и спрашивает недостающий `ADMIN_BACKEND_SHARED_TOKEN` перед запуском `docker compose`. В `noadmin` launcher может спросить browser-facing backend URL, сохранить `PUBLIC_DOWNLOAD_BASE_URL`, `ADMIN_SHARED_TOKEN` и вычислить `ADMIN_BACKEND_PORT` для нового HTTP facade path. В VPN-path shell launcher переносит WebSocket metadata из join token в `.env` и делает `docker compose --profile vpn up -d --force-recreate wstunnel-client vpn-client`, чтобы новый token/`wg0.conf` гарантированно применялся и заново писал `.ready`. Legacy `wg0.conf` без `# VPN_*` metadata тоже поддерживается: launcher использует старый peer `Endpoint` как fallback и мигрирует конфиг на локальный `127.0.0.1:<VPN_CLIENT_LOCAL_PORT>`. В `noadmin + VPN` shell launcher заранее проверяет `VPN_WS_PORT`, при занятом TCP-порте выбирает первый свободный fallback из `MODELLINE_VPN_WS_PORT_CANDIDATES` или встроенного списка и прописывает `REDPANDA_EXTERNAL_HOST` и все backend `*_BIND_ADDR` на `10.44.0.1` до рестарта сервисов. |
| `stop.sh` / `stop.ps1` | Остановка сервисов, clean/prune режимы. `clean` для stateful сервисов удаляет не только Docker volumes, но и repo-local runtime-каталоги в `../.runtime-data/` (`microservice_infra`, `microservice_account`, `microservice_data`, `microservice_analitic`) |
| `restart.sh` / `restart.ps1` | `git pull` + пересборка + перезапуск. Поддерживают `core`, `noadmin`, `onlyadmin`, `full`, `deps`, `api`. Для multi-service режима сначала синхронно обновляют/поднимают `microservice_infra`, потом перезапускают остальные сервисы параллельно; `git pull` выполняется один раз до fan-out. Linux-версия заранее создаёт repo-local bind-mount каталоги в `.runtime-data/` и нормализует права записи перед `docker compose up`. В `onlyadmin` могут принять backend host/IP аргументом или, если он не задан, заранее спросить его в консоли, перезаписать derived `ONLINE_*`, предложить `ADMIN_BACKEND_BASE_URL` и спросить недостающий `ADMIN_BACKEND_SHARED_TOKEN` перед rebuild+up. В `noadmin` launcher может спросить browser-facing backend URL, сохранить `PUBLIC_DOWNLOAD_BASE_URL`, `ADMIN_SHARED_TOKEN` и вычислить `ADMIN_BACKEND_PORT` для нового HTTP facade path. В VPN-path shell launcher переносит WebSocket metadata из join token в `.env` и делает `docker compose --profile vpn up -d --force-recreate wstunnel-client vpn-client`, чтобы ожидание `.ready` не зависало на уже работающем старом контейнере. Legacy `wg0.conf` без `# VPN_*` metadata больше не ломает shell path под `set -e`: launcher читает отсутствующие ключи как пустые, затем берёт fallback из старого peer `Endpoint` и мигрирует конфиг на локальный dial через `wstunnel-client`. В `noadmin + VPN` shell launcher заранее проверяет `VPN_WS_PORT`, при занятом TCP-порте выбирает первый свободный fallback из `MODELLINE_VPN_WS_PORT_CANDIDATES` или встроенного списка и прописывает `REDPANDA_EXTERNAL_HOST` и все backend `*_BIND_ADDR` на `10.44.0.1` до рестарта сервисов. |
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
- изменение того, какие host-порты публикуются обычным `start` и split-режимами (`local/full` — `8501` от infra-nginx; `onlyadmin` — `443` от `admin-online` на отдельном хосте по умолчанию)
