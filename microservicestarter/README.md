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
./start.sh all noadmin            # запустить всё, кроме admin (+ VPN сервер, если VPN_SERVER_URL задан)
./start.sh all onlyadmin          # запустить только online-head admin (переиспользует wg0.conf, если есть)
./start.sh all onlyadmin <TOKEN>  # 1-й запуск: TOKEN = VPN join token (base64) или plain backend IP
./restart.sh                      # git pull + перезапустить все
./restart.sh all noadmin          # git pull + всё, кроме admin
./restart.sh all onlyadmin        # git pull + только online-head admin
./restart.sh all onlyadmin <TOKEN> # обновить VPN join token/IP и пересобрать admin-online
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
`ONLINE_REDIS_URL`).

Канонический browser URL для этого режима: `http://<admin-host>:443/admin/`.
Не используй как ориентир bare `http://<admin-host>:443/`: `admin-online`
работает с `basePath=/admin`. На backend-хосте в режиме `noadmin` порт
`8501` не является UI-входом admin-панели.

**Containerized VPN (рекомендуется):** При `VPN_SERVER_URL` в `microservice_infra/.env`:

```bash
# Backend-хост:
./start.sh all noadmin
# → поднимает WireGuard-сервер и печатает JOIN TOKEN

# Admin-хост (первый запуск):
./start.sh all onlyadmin <JOIN_TOKEN>   # TOKEN = base64 строка из вывода выше

# Admin-хост (последующие перезапуски — токен больше не нужен):
./start.sh all onlyadmin
./restart.sh all onlyadmin
```

Launcher автоматически декодирует join token, поднимает `vpn-client`, ждёт туннеля и выставляет `ONLINE_*` на `10.44.0.1:*`. Подробнее: [../microservice_infra/VPN_CONTAINERIZED.md](../microservice_infra/VPN_CONTAINERIZED.md).

При `onlyadmin <JOIN_TOKEN>` и при повторном `onlyadmin` с уже сохранённым
`wg0.conf` launcher теперь делает `--force-recreate` для `vpn-client`.
Иначе уже работающий контейнер мог не перечитать новый конфиг, а `.ready`
после удаления так и не появлялся, из-за чего shell-ждал туннель бесконечно.

В `noadmin + VPN` shell launcher теперь также заранее прописывает backend-side
env для private path: `REDPANDA_EXTERNAL_HOST=10.44.0.1`,
`REDPANDA_BIND_ADDR=10.44.0.1`, `MINIO_BIND_ADDR=10.44.0.1`,
`ACCOUNT_BIND_ADDR=10.44.0.1`, `GATEWAY_BIND_ADDR=10.44.0.1` до рестарта
backend-сервисов. Это переводит WG/private binding в repo-managed flow и
снижает зависимость split deployment от ручной host firewall-настройки.

**Manual backend IP (без VPN):** Launcher принимает backend host/IP аргументом:

- Linux: `./start.sh all onlyadmin 10.44.0.1`

Если host не передан и wg0.conf отсутствует, launcher спрашивает его
в консоли и сохраняет в `microservice_admin/.env` как
`ONLINE_BACKEND_HOST`, автоматически выводя:

- `ONLINE_KAFKA_BOOTSTRAP_SERVERS=<host>:9092`
- `ONLINE_REDPANDA_ADMIN_URL=<host>:9644`
- `ONLINE_ACCOUNT_URL=<host>:7510`
- `ONLINE_GATEWAY_URL=<host>:7520`
- `ONLINE_MINIO_URL=<host>:9000`

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
