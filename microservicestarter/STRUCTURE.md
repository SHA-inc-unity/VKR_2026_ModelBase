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
| `start.sh` / `start.ps1` | Запуск всех или выбранных сервисов. Поддерживают `core`, `noadmin`, `onlyadmin`, `full`, `scheduler`, `build`, `logs`. Если у сервиса отсутствует `.env`, launcher восстанавливает его из `.env.example`, когда шаблон существует. Для multi-service запуска сначала поднимают `microservice_infra`, затем fan-out запускают остальные сервисы параллельно. Linux-версия заранее создаёт repo-local bind-mount каталоги в `.runtime-data/` и нормализует права записи перед `docker compose up`. Для split deployment режим `onlyadmin` поднимает пару `admin-online` + `admin-online-proxy` без принудительного `--build`; rebuild остаётся в `restart.*`. Дополнительно `onlyadmin` принимает backend host/IP аргументом или, если он не задан, заранее запрашивает его в консоли, выводит derived `ONLINE_*`, предлагает `ADMIN_BACKEND_BASE_URL`, при пустом `ADMIN_BACKEND_TLS_INSECURE` + `https://` facade записывает `ADMIN_BACKEND_TLS_INSECURE=1`, дописывает browser-facing defaults `ADMIN_HTTP_PORT=80`, `ADMIN_HTTPS_PORT=443`, `ADMIN_PRIMARY_DOMAIN=sha-trade.tech`, `ADMIN_SECONDARY_DOMAIN=www.sha-trade.tech`, `ADMIN_TLS_CERT_PATH`, `ADMIN_TLS_KEY_PATH`, а bash-версия дополнительно зеркалит derived endpoints в direct runtime env (`ACCOUNT_URL`, `GATEWAY_URL`, `REDPANDA_ADMIN_URL`, `MINIO_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `BACKEND_CONNECTION_TARGET`) и печатает полный env summary в консоль перед запуском `docker compose`. В `noadmin` launcher может спросить browser-facing backend URL, сохранить `PUBLIC_DOWNLOAD_BASE_URL` и вычислить `ADMIN_BACKEND_PORT` для HTTP facade path. Admin auth идёт через login-only account с ролью `admin`, без обмена общим ключом между хостами. Docker image prune теперь защищён межпроцессным lock-ом, поэтому параллельный fan-out не должен падать и шуметь на конкурентном cleanup. Bash-версия перед `docker compose up` дополнительно делает preflight host-port checks и заранее ловит конфликты с внешними контейнерами/процессами, но не должна считать уже запущенные контейнеры того же compose-проекта внешними. |
| `stop.sh` / `stop.ps1` | Остановка сервисов, clean/prune режимы. `clean` для stateful сервисов удаляет не только Docker volumes, но и repo-local runtime-каталоги в `../.runtime-data/` (`microservice_infra`, `microservice_account`, `microservice_data`, `microservice_analitic`). Скрипт управляет только compose-стеками ModelLine и не удаляет сторонние контейнеры/чужие compose-проекты на том же хосте. |
| `restart.sh` / `restart.ps1` | `git pull` + пересборка + перезапуск. Поддерживают `core`, `noadmin`, `onlyadmin`, `full`, `deps`, `api`. Если у сервиса отсутствует `.env`, launcher восстанавливает его из `.env.example`, когда шаблон существует. Для multi-service режима сначала синхронно обновляют/поднимают `microservice_infra`, потом перезапускают остальные сервисы параллельно; `git pull` выполняется один раз до fan-out. Linux-версия заранее создаёт repo-local bind-mount каталоги в `.runtime-data/` и нормализует права записи перед `docker compose up`. В `onlyadmin` могут принять backend host/IP аргументом или, если он не задан, заранее спросить его в консоли, перезаписать derived `ONLINE_*`, предложить `ADMIN_BACKEND_BASE_URL`, при пустом `ADMIN_BACKEND_TLS_INSECURE` + `https://` facade записать `ADMIN_BACKEND_TLS_INSECURE=1`, а также дописать browser-facing defaults `ADMIN_HTTP_PORT=80`, `ADMIN_HTTPS_PORT=443`, `ADMIN_PRIMARY_DOMAIN=sha-trade.tech`, `ADMIN_SECONDARY_DOMAIN=www.sha-trade.tech`, `ADMIN_TLS_CERT_PATH`, `ADMIN_TLS_KEY_PATH` перед rebuild+up. Bash-версия дополнительно зеркалит derived endpoints в direct runtime env (`ACCOUNT_URL`, `GATEWAY_URL`, `REDPANDA_ADMIN_URL`, `MINIO_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `BACKEND_CONNECTION_TARGET`) и печатает полный env summary в консоль, чтобы `restart.sh` не требовал ручной правки `microservice_admin/.env`. В `onlyadmin` launcher поднимает `admin-online` вместе с `admin-online-proxy`, а не только один Next.js контейнер. В `noadmin` launcher может спросить browser-facing backend URL, сохранить `PUBLIC_DOWNLOAD_BASE_URL` и вычислить `ADMIN_BACKEND_PORT` для HTTP facade path. Admin auth идёт через login-only account с ролью `admin`, без обмена общим ключом между хостами. Bash-ветка parallel restart теперь в финальной ошибке перечисляет упавшие сервисы, а не только общий failure summary, а docker image prune защищён межпроцессным lock-ом, чтобы не ловить конкурентный cleanup в fan-out. Bash-версия перед `docker compose up` дополнительно делает preflight host-port checks и заранее ловит конфликты с внешними контейнерами/процессами, но пропускает уже работающие контейнеры того же compose-проекта, включая Docker publish ranges вроде `9000-9001`, чтобы `restart` оставался идемпотентным для CI/CD. |
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
- изменение того, какие host-порты публикуются обычным `start` и split-режимами (`local/full` — `8501` от infra-nginx; `onlyadmin` — `80/443` от `admin-online-proxy` на отдельном хосте по умолчанию)
