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
| `start.sh` / `start.ps1` | Запуск всех или выбранных сервисов. Поддерживают `core`, `noadmin`, `onlyadmin`, `full`, `scheduler`, `build`, `logs`. Если у сервиса отсутствует `.env`, launcher восстанавливает его из `.env.example`, когда шаблон существует. Для multi-service запуска сначала поднимают `microservice_infra`, затем fan-out запускают остальные сервисы параллельно. Linux-версия заранее создаёт repo-local bind-mount каталоги в `.runtime-data/` и нормализует права записи перед `docker compose up`. Для split deployment режим `onlyadmin` теперь только поднимает `admin-online` без принудительного `--build`; rebuild остаётся в `restart.*`. Дополнительно `onlyadmin` принимает backend host/IP аргументом или, если он не задан, заранее запрашивает его в консоли, выводит derived `ONLINE_*`, предлагает `ADMIN_BACKEND_BASE_URL`, спрашивает backend-generated `ADMIN_BACKEND_SHARED_TOKEN` и при пустом `ADMIN_BACKEND_TLS_INSECURE` + `https://` facade записывает `ADMIN_BACKEND_TLS_INSECURE=1` перед запуском `docker compose`. В `noadmin` launcher может спросить browser-facing backend URL, автоматически сгенерировать отсутствующий `ADMIN_SHARED_TOKEN`, сохранить `PUBLIC_DOWNLOAD_BASE_URL` и вычислить `ADMIN_BACKEND_PORT` для нового HTTP facade path. Docker image prune теперь защищён межпроцессным lock-ом, поэтому параллельный fan-out не должен падать и шуметь на конкурентном cleanup. Bash-версия перед `docker compose up` дополнительно делает preflight host-port checks и заранее ловит конфликты с внешними контейнерами/процессами, но не должна считать уже запущенные контейнеры того же compose-проекта внешними. |
| `stop.sh` / `stop.ps1` | Остановка сервисов, clean/prune режимы. `clean` для stateful сервисов удаляет не только Docker volumes, но и repo-local runtime-каталоги в `../.runtime-data/` (`microservice_infra`, `microservice_account`, `microservice_data`, `microservice_analitic`). Скрипт управляет только compose-стеками ModelLine и не удаляет сторонние контейнеры/чужие compose-проекты на том же хосте. |
| `restart.sh` / `restart.ps1` | `git pull` + пересборка + перезапуск. Поддерживают `core`, `noadmin`, `onlyadmin`, `full`, `deps`, `api`. Если у сервиса отсутствует `.env`, launcher восстанавливает его из `.env.example`, когда шаблон существует. Для multi-service режима сначала синхронно обновляют/поднимают `microservice_infra`, потом перезапускают остальные сервисы параллельно; `git pull` выполняется один раз до fan-out. Linux-версия заранее создаёт repo-local bind-mount каталоги в `.runtime-data/` и нормализует права записи перед `docker compose up`. В `onlyadmin` могут принять backend host/IP аргументом или, если он не задан, заранее спросить его в консоли, перезаписать derived `ONLINE_*`, предложить `ADMIN_BACKEND_BASE_URL`, спросить backend-generated `ADMIN_BACKEND_SHARED_TOKEN` и при пустом `ADMIN_BACKEND_TLS_INSECURE` + `https://` facade записать `ADMIN_BACKEND_TLS_INSECURE=1` перед rebuild+up. В `noadmin` launcher может спросить browser-facing backend URL, автоматически сгенерировать отсутствующий `ADMIN_SHARED_TOKEN`, сохранить `PUBLIC_DOWNLOAD_BASE_URL` и вычислить `ADMIN_BACKEND_PORT` для нового HTTP facade path. Bash-ветка parallel restart теперь в финальной ошибке перечисляет упавшие сервисы, а не только общий failure summary, а docker image prune защищён межпроцессным lock-ом, чтобы не ловить конкурентный cleanup в fan-out. Bash-версия перед `docker compose up` дополнительно делает preflight host-port checks и заранее ловит конфликты с внешними контейнерами/процессами, но пропускает уже работающие контейнеры того же compose-проекта, включая Docker publish ranges вроде `9000-9001`, чтобы `restart` оставался идемпотентным для CI/CD. |
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
