# microservicestarter

Общий менеджер для запуска, остановки и обновления микросервисов ModelLine.

## Документация для агентов

- [STRUCTURE.md](STRUCTURE.md) — карта файлов, скриптов и режимов launcher-а
- [../docs/agents/services/microservicestarter.md](../docs/agents/services/microservicestarter.md) — профиль каталога для agent workflow
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first маршрут работы

## Реестр сервисов

`services.conf` — текстовый файл, по одному сервису на строку:

```text
<service_name>  <path_from_repo_root>
```

Текущие сервисы:

- `microservice_infra` — общая инфраструктура платформы
- `microservice_data` — сервис данных и dataset jobs
- `microservice_admin` — admin UI
- `microservice_analitic` — аналитика и ML-модели
- `microservice_account` — сервис аккаунтов и авторизации
- `microservice_gateway` — mobile BFF gateway

## Быстрый старт

**Linux/macOS:**

```bash
./start.sh                        # запустить все сервисы
./start.sh all noadmin            # запустить всё, кроме admin
./start.sh all onlyadmin          # запустить только online-head admin
./start.sh all onlyadmin <HOST>   # сразу указать backend host/IP для admin-online
./restart.sh                      # git pull + перезапустить все
./restart.sh all noadmin          # git pull + всё, кроме admin
./restart.sh all onlyadmin        # git pull + только online-head admin
./restart.sh all onlyadmin <HOST> # обновить backend host/IP и пересобрать admin-online
./stop.sh                         # остановить все
./status.sh                       # посмотреть состояние
./update.sh                       # только git pull
```

**Windows (PowerShell):**

```powershell
.\start.ps1                       # запустить все сервисы
.\start.ps1 -Mode noadmin         # запустить всё, кроме admin
.\start.ps1 -Mode onlyadmin       # запустить только online-head admin
.\start.ps1 -Mode onlyadmin -BackendHost 10.44.0.1   # сразу записать backend host/IP для admin-online
.\restart.ps1                     # git pull + перезапустить все
.\restart.ps1 -Mode noadmin       # git pull + всё, кроме admin
.\restart.ps1 -Mode onlyadmin     # git pull + только online-head admin
.\restart.ps1 -Mode onlyadmin -BackendHost 10.44.0.1 # обновить backend host/IP и пересобрать admin-online
.\stop.ps1                        # остановить все
.\status.ps1                      # посмотреть состояние
.\update.ps1                      # только git pull
```

Подробная документация и таблица режимов — в корневом [README.md](../README.md).

Операционное поведение launcher-а:

- `start` и `restart` теперь автоматически восстанавливают отсутствующий `.env` из `.env.example`, если шаблон существует у сервиса
- в multi-service fan-out dangling Docker image cleanup больше не должен конфликтовать между child-процессами: launcher ставит межпроцессный lock и пропускает параллельный `docker image prune`, если cleanup уже выполняется другим launcher-процессом
- `stop.sh` / `stop.ps1` управляют только compose-стеками ModelLine из `services.conf` и не трогают сторонние контейнеры или чужие compose-проекты на том же хосте
- bash-версии `start.sh` / `restart.sh` перед `docker compose up` теперь делают preflight host-port check для publish-единиц launcher-а (`8443`, `8501`, `7510`, `7520`, infra ports и `ADMIN_PORT` в `onlyadmin`) и падают сразу с явным сообщением, если порт занят внешним контейнером/процессом
- preflight host-port check не должен блокировать обычный `restart`: уже запущенные контейнеры того же compose-проекта считаются своими и допускаются, пока конфликт реально не идёт от внешнего контейнера/процесса

## Repo-local runtime data

Stateful Docker-сервисы по умолчанию хранят runtime-данные в каталоге
репозитория, а не в Docker named volumes:

- `../.runtime-data/microservice_infra/redpanda`
- `../.runtime-data/microservice_infra/minio`
- `../.runtime-data/microservice_account/postgres`
- `../.runtime-data/microservice_account/redis`
- `../.runtime-data/microservice_data/postgres`
- `../.runtime-data/microservice_analitic/redis`
- `../.runtime-data/microservice_analitic/models`

Режим `clean` у `stop.ps1` / `stop.sh` для этих сервисов удаляет и Docker
volumes, и соответствующие каталоги внутри `.runtime-data/`.

На Linux `start.sh` и `restart.sh` перед `docker compose up` автоматически
создают эти каталоги и нормализуют права записи для bind mounts. Это нужно,
чтобы non-root контейнеры вроде `redpanda` корректно стартовали на свежем
сервере после клона репозитория.

## Режимы restart.ps1

| Режим | Команда | Поведение |
| ----- | ------- | --------- |
| `core` (default) | `.\restart.ps1` | `docker compose up -d --build` — атомарная сборка + запуск |
| `noadmin` | `.\restart.ps1 -Mode noadmin` | git pull + запуск всех сервисов, кроме `microservice_admin` |
| `onlyadmin` | `.\restart.ps1 -Mode onlyadmin` | git pull + запуск только online-head admin (`admin-online`) |
| `api` | `.\restart.ps1 -Service microservice_analitic -Mode api` | `docker compose up -d --no-deps --build api` — только api-сервис |
| `full` | `.\restart.ps1 -Service microservice_analitic -Mode full` | `docker compose --profile scheduler up -d --build` — со scheduler |
| `deps` | `.\restart.ps1 -Service microservice_analitic -Mode deps` | двухшаговый: сначала `build --no-cache base`, затем `up -d` |
| `postgres` | `.\restart.ps1 -Service microservice_analitic -Mode postgres` | перезапуск postgres |
| `redis` | `.\restart.ps1 -Service microservice_analitic -Mode redis` | перезапуск redis |

> **Примечание:** в режимах `core`, `api`, `full` используется атомарная команда `up --build`.
> Отдельный вызов `docker compose build` применяется только в режиме `deps` (для пересборки base-образа без кэша).

## Split deployment

Launcher теперь поддерживает два разделённых сценария:

1. `noadmin` — backend-хост поднимает infra/data/analitic/account/gateway без локального admin.
2. `onlyadmin` — отдельный хост поднимает только `microservice_admin` как online-head.

В режиме `onlyadmin` используется compose-service `admin-online`, который
публикует свой `443:3000` напрямую по умолчанию (`ADMIN_PORT` можно переопределить) и читает внешние адреса из namespace
`ONLINE_*` (`ONLINE_KAFKA_BOOTSTRAP_SERVERS`, `ONLINE_REDPANDA_ADMIN_URL`,
`ONLINE_ACCOUNT_URL`, `ONLINE_GATEWAY_URL`, `ONLINE_MINIO_URL`,
`ONLINE_REDIS_URL`). Для нового HTTPS facade launcher дополнительно хранит в
`microservice_admin/.env` `ADMIN_BACKEND_BASE_URL` и `ADMIN_BACKEND_SHARED_TOKEN`.

Primary split path идёт через backend HTTPS facade на `ADMIN_BACKEND_BASE_URL`.

Канонический browser URL для этого режима: `http://<admin-host>:443/admin/`.
Не используй как ориентир bare `http://<admin-host>:443/`: `admin-online`
работает с `basePath=/admin`. На backend-хосте в режиме `noadmin` порт
`8501` не является UI-входом admin-панели.

Launcher принимает backend host/IP аргументом:

- Linux: `./start.sh all onlyadmin backend.example.com`

Если host не передан, launcher спрашивает его
в консоли и сохраняет в `microservice_admin/.env` как
`ONLINE_BACKEND_HOST`, автоматически выводя:

- `ONLINE_KAFKA_BOOTSTRAP_SERVERS=<host>:9092`
- `ONLINE_REDPANDA_ADMIN_URL=<host>:9644`
- `ONLINE_ACCOUNT_URL=<host>:7510`
- `ONLINE_GATEWAY_URL=<host>:7520`
- `ONLINE_MINIO_URL=<host>:9000`

Для HTTP facade launcher также:

- предлагает `ADMIN_BACKEND_BASE_URL` с default `https://<host>:8443`
- спрашивает `ADMIN_BACKEND_SHARED_TOKEN`, только если он ещё не задан, и ожидает сюда токен, уже сгенерированный на backend-host
- прокидывает оба ключа в `admin-online`, чтобы split mode действительно переключился с direct Kafka path на backend facade

На backend-хосте режим `noadmin` теперь тоже может донастроить недостающий
минимум для нового split-path ещё до старта compose:

- спросить browser-facing base URL backend-хоста и записать его в `microservice_data/.env` как `PUBLIC_DOWNLOAD_BASE_URL`
- автоматически сгенерировать отсутствующий `ADMIN_SHARED_TOKEN`, сохранить его в `microservice_gateway/.env` и вывести напоминание передать это же значение на admin-host как `ADMIN_BACKEND_SHARED_TOKEN`
- вывести порт из этого URL и сохранить его в `microservice_infra/.env` как `ADMIN_BACKEND_PORT`

Поведение split admin-head теперь различается между `start` и `restart`:

- `start ... onlyadmin` делает только `docker compose --profile online up -d admin-online` без принудительной пересборки образа
- `restart ... onlyadmin` по-прежнему делает rebuild и затем поднимает `admin-online`

Это нужно, чтобы обычный `start` на удалённом admin-хосте не провоцировал тяжёлую Next.js пересборку и не создавал лишний operational risk для слабых VPS/SSH-сессий.

Если `microservice_admin/.env` уже существует, а `BackendHost` не передан,
launcher покажет текущее значение `ONLINE_BACKEND_HOST` как default и даст
быстро заменить его при запуске.

## Параллельный запуск

Для multi-service сценариев launcher больше не гонит все сервисы строго
по одному. Когда выбран не один сервис, он работает так:

1. сначала синхронно поднимает `microservice_infra`, чтобы гарантированно
   появилась общая сеть и базовая инфраструктура;
2. затем запускает оставшиеся выбранные сервисы параллельно отдельными
   дочерними процессами launcher-а.

Это ускоряет общий `build + up`, но не меняет поведение одиночного
сервиса: если запущен один target, его compose-логика остаётся прежней.
Для `restart` `git pull` по-прежнему выполняется один раз на весь репозиторий
до параллельного fan-out.

## Конфликты host-портов

Если на хосте уже работает чужой контейнер или процесс, который держит один из
портов ModelLine, launcher теперь завершится до `docker compose up` с явным
сообщением о конфликте и подсказкой, какую `.env`-переменную менять.

Это особенно важно для split deployment на backend-host:

- `microservice_infra` ожидает свободный `ADMIN_BACKEND_PORT` (по умолчанию `8443`)
- `stop.sh` не удаляет чужие контейнеры вроде внешних `nginx`/`mc-proxy`; их нужно останавливать отдельно или переносить на другой порт
- если внешний сервис должен остаться, переопредели конфликтующий порт в `.env` соответствующего сервиса и затем повтори `start` / `restart`
- уже работающие контейнеры самого ModelLine-стека не требуют ручного `stop`: `restart` должен видеть их как собственный compose-проект и переиспользовать lifecycle через `docker compose up -d --build`

## Внешний вход 8501 — без интерактивных prompt'ов

`microservice_infra` поднимает nginx-вход на host-порте `8501` (override
через `NGINX_PORT`) автоматически при обычном `start` / `restart`.
Никаких опциональных profile-флагов или интерактивных вопросов
«пробросить ли nginx?» больше нет — единая внешняя топология
(`/admin/*` → admin:3000, `/modelline-blobs/*` → minio:9000) включена в
обычный compose-стек. Это требование задачи: локальный запуск должен
поднимать нужную схему штатно, без ручных дополнительных шагов.

В обычном local/full stack `microservice_admin` сам наружу не публикуется —
его `3000` живёт только в `modelline_net`, а browser-вход идёт через
`http://localhost:8501/admin/`. В split deployment это правило не действует:
режим `onlyadmin` поднимает отдельный `admin-online` и публикует `443:3000`
на своей машине. То есть в split deployment UI надо открывать именно на
admin-host: `http://<admin-host>:443/admin/`; backend-host:8501 остаётся
инфраструктурным ingress-ом и не должен использоваться как адрес панели.
