# ModelLine — Микросервисная архитектура

Монорепозиторий платформы ModelLine. В runtime-контур входят инфраструктурный слой, сервис данных, admin UI, ML-сервис аналитики, сервис аккаунтов и mobile gateway. Общий запуск, остановка и обновление сервисов выполняет **microservicestarter**.

---

## Runtime-сервисы

| Сервис | Технологии | Порты | Описание |
| ------ | ---------- | ----- | -------- |
| [microservice_infra](microservice_infra/README.md) | Docker Compose, Redpanda, MinIO, Nginx | `9092`, `8080`, `9000`, `9001`, `8501`, `8443` | Общая инфраструктура платформы: Kafka, S3 claim-check, локальный ingress/download endpoint backend-стека и HTTPS admin facade для split deployment |
| [microservice_data](microservice_data/README.md) | .NET 8, ASP.NET Core, PostgreSQL, Kafka, MinIO | `8100` | Владелец рыночных данных, датасета, export и фоновых jobs |
| [microservice_admin](microservice_admin/README.md) | Next.js 14, React 18, TypeScript, Kafka, Redis | `3000` (local stack), `443` (`onlyadmin`, default) | Admin UI и операторская панель платформы; не исполняет jobs, а только управляет и наблюдает jobs других сервисов. В local-стеке идёт через infra-nginx, в split deployment может публиковаться как отдельная online-head нода |
| [microservice_analitic](microservice_analitic/README.md) | Python 3.12, CatBoost, FastAPI, PostgreSQL, Redis | API: `8000` | ML-сервис: обучение, прогнозы, аналитика рынка |
| [microservice_account](microservice_account/README.md) | .NET 8, ASP.NET Core, PostgreSQL, Redis | `7510` | Сервис аутентификации и управления аккаунтами (Clean Architecture) |
| [microservice_gateway](microservice_gateway/README.md) | .NET 8, ASP.NET Core | `7520` | Mobile BFF Gateway — маршрутизация и агрегация запросов |

---

## Общие каталоги

| Каталог | Назначение |
| ------- | ---------- |
| [microservicestarter](microservicestarter/README.md) | Единый launcher и операционные скрипты для всех сервисов |
| [shared](shared/README.md) | Общий Python-пакет с контрактами и messaging-утилитами |
| [docs/agents](docs/agents/README.md) | Docs-first структура и маршрут чтения для агентной разработки |

---

## Структура репозитория

```text
/
├── AGENTS.md                    # Глобальные правила работы агентов
├── promt_agent.md               # Краткий рабочий дневник агента
├── .runtime-data/               # Локальные bind-mounted runtime-данные сервисов (создаётся Docker, в git не хранится)
├── docs/agents/                 # Markdown-опоры для агентной разработки
├── .github/instructions/        # Агентские file instructions
│
├── microservice_infra/          # Redpanda + MinIO + Nginx + shared network
├── microservice_data/           # Data service (.NET 8, PostgreSQL, Kafka jobs)
├── microservice_admin/          # Admin UI (Next.js, Kafka, Redis)
├── microservice_analitic/       # ML service (Python, FastAPI, training, anomaly)
├── microservice_account/        # Auth service (.NET 8, JWT, PostgreSQL)
├── microservice_gateway/        # Mobile BFF gateway (.NET 8)
├── microservicestarter/         # Общий менеджер для всех микросервисов
├── shared/                      # Общий Python-пакет `modelline_shared`
├── README.md                    # Этот файл
└── STRUCTURE.md                 # Корневая карта репозитория
```

---

## Внешний вход и схема download

В репозитории теперь поддерживаются **две топологии запуска**.

### 1. Local/full stack

По умолчанию browser-facing вход держит nginx из `microservice_infra`
на host-порту `8501`:

| URL                                       | Куда проксируется                       |
|-------------------------------------------|-----------------------------------------|
| `http://localhost:8501/`                  | 301 → `/admin/`                         |
| `http://localhost:8501/admin/*`           | `admin:3000` (Next.js, basePath=/admin) |
| `http://localhost:8501/admin/api/events`  | `admin:3000` (SSE, без буферизации)     |
| `http://localhost:8501/modelline-blobs/*` | `minio:9000` (signed downloads)         |

В этом режиме `microservice_admin` сам наружу не публикуется: браузер
идёт через infra-nginx, а большие dataset-export файлы скачиваются через
тот же origin. `microservice_data` подписывает browser-bound presigned
URL через `PUBLIC_DOWNLOAD_BASE_URL` (по умолчанию `http://localhost:8501`),
nginx стримит `/modelline-blobs/*` напрямую из MinIO, байты через admin
runtime не идут.

### 2. Split deployment: `noadmin` + `onlyadmin`

Backend-хост можно поднять в режиме `noadmin`: локально стартуют infra,
data, analitic, account и gateway, но без `microservice_admin`. Отдельно
можно поднять `microservice_admin` в режиме `onlyadmin` на другой машине
как **online-head**: admin сам публикует `443:3000`, остаётся на basePath
`/admin` и работает против внешних Kafka/HTTP endpoints через namespace
переменных `ONLINE_*`.

Важное уточнение для split deployment:

- рабочая UI-точка admin-панели находится на **admin-host** по адресу `http://<admin-host>:443/admin/`
- в текущем compose `admin-online` публикует plain HTTP на `443`; URL вида `https://<admin-host>:443/admin/` не будет работать, пока перед `admin-online` не появится отдельный TLS reverse proxy/terminator
- bare URL `http://<admin-host>:443/` не является канонической точкой входа, потому что `admin-online` работает с `basePath=/admin`
- на **backend-host** в режиме `noadmin` порт `8501` не должен считаться адресом admin-панели; там остаётся только infra-nginx/download ingress, а локальный `/admin/*` без поднятого `microservice_admin` не является рабочей UI-точкой

Primary split path для этих двух машин — **HTTPS admin facade на backend-host `:8443`** через `ADMIN_BACKEND_BASE_URL` и `ADMIN_SHARED_TOKEN`.

Backend-host теперь поднимает этот `:8443` без ручной подготовки сертификатов:
`microservice_infra` запускает one-shot `nginx-cert-init`, который генерирует
self-signed `tls.crt` / `tls.key` в `ADMIN_BACKEND_CERTS_DIR`, если каталог пустой.
`admin-online` по умолчанию принимает такой сертификат через
`ADMIN_BACKEND_TLS_INSECURE=1`, поэтому основной split path снова авторазворачиваемый.
После установки доверенного backend cert переведи admin-host на
`ADMIN_BACKEND_TLS_INSECURE=0`.

Критичный technical detail для split deployment: backend Kafka broker не должен advertise'ить `localhost:9092`. В `microservice_infra/docker-compose.yml` внешний advertise address теперь configurable через `REDPANDA_EXTERNAL_HOST` и `REDPANDA_EXTERNAL_PORT`; для remote admin-head туда нужно подставлять WG IP или private DNS backend-хоста.

В split deployment download path остаётся прямым и zero-byte для admin:
remote admin-head получает `presigned_url` от data-сервиса и браузер
качает CSV/ZIP напрямую с backend ingress-а, заданного через
`PUBLIC_DOWNLOAD_BASE_URL`. Это может быть другой host/origin, чем тот,
на котором живёт admin-head.

## Правила запуска двух серверов

Для production split deployment придерживайся одной и той же схемы:

1. backend-host работает в режиме `noadmin`
2. admin-host работает в режиме `onlyadmin`
3. сначала обновляется и перезапускается backend-host, только потом admin-host

Рекомендуемый порядок для двух Linux-серверов:

```bash
# backend-host
cd /path/to/ModelLine/microservicestarter
./restart.sh all noadmin

# после backend restart проверь HTTP facade и backend APIs
curl -skf https://<backend-host-or-domain>:8443/health && echo OK
curl -sf http://<backend-host-or-domain>:7510/health && echo OK
curl -sf http://<backend-host-or-domain>:7520/health && echo OK

# admin-host
cd /path/to/ModelLine/microservicestarter
./restart.sh all onlyadmin <backend-host-or-domain>
```

Практическое правило:

- если менялся код, `Dockerfile`, `docker-compose.yml` или runtime `.env`, используй `restart`
- если на admin-host менялся только backend host/IP или нужно просто поднять уже собранный `admin-online`, используй `start ... onlyadmin <backend-host>` без rebuild
- для split deployment используй browser-facing backend host/domain для `ADMIN_BACKEND_BASE_URL`

## Правила работы с конфигом

`services.conf` — это общий реестр сервисов репозитория. Он должен оставаться одинаковым на обоих хостах и не используется для выбора роли сервера. Разделение backend/admin делается только launcher-режимами `noadmin` и `onlyadmin`, а не удалением строк из `services.conf`.

Host-specific настройки хранятся в `.env` конкретного сервиса:

- `microservice_infra/.env` — внешний advertise path, bind address инфраструктуры и TLS-port backend facade (`ADMIN_BACKEND_PORT`)
- `microservice_account/.env` — bind address и порт account API
- `microservice_gateway/.env` — bind address, порт gateway API и `ADMIN_SHARED_TOKEN` для admin facade
- `microservice_data/.env` — `PUBLIC_DOWNLOAD_BASE_URL` для browser-facing download origin
- `microservice_admin/.env` — `ONLINE_BACKEND_HOST`, derived `ONLINE_*`, `ADMIN_BACKEND_BASE_URL`, `ADMIN_BACKEND_SHARED_TOKEN` и `ADMIN_BACKEND_TLS_INSECURE` для remote admin-head

Правила:

- `.env.example` — шаблон, его коммитят в репозиторий; рабочие `.env` — локальные для каждого хоста
- изменения `.env` не подхватываются на лету: после правки нужно заново выполнить `start` или `restart` для соответствующего сервиса/хоста
- в split deployment browser-facing backend URL живёт в `PUBLIC_DOWNLOAD_BASE_URL` и `ADMIN_BACKEND_BASE_URL`
- для ограничения publish-ed private ports на backend-host используй bind-address переменные `REDPANDA_BIND_ADDR`, `MINIO_BIND_ADDR`, `ACCOUNT_BIND_ADDR`, `GATEWAY_BIND_ADDR`
- launcher для split deployment теперь сам собирает недостающие значения: на backend-host в `noadmin` — browser-facing base URL, `ADMIN_BACKEND_PORT` и, если нужно, автоматически генерирует `ADMIN_SHARED_TOKEN`; на admin-host в `onlyadmin` — backend host, `ADMIN_BACKEND_BASE_URL` и спрашивает уже сгенерированный backend token для `ADMIN_BACKEND_SHARED_TOKEN`
- backend TLS bootstrap больше не требует ручного `openssl req ...`: если `ADMIN_BACKEND_CERTS_DIR` пуст, `microservice_infra` автогенерирует self-signed cert на первом старте; existing cert files всегда приоритетнее autogenerated pair
- самый безопасный путь для admin-host — передавать backend адрес аргументом launcher-а (`./start.sh all onlyadmin <backend-host>` / `./restart.sh all onlyadmin <backend-host>`), а не редактировать вручную `ONLINE_*` и `ADMIN_BACKEND_*`

## Документация для агентов

В репозитории включён docs-first workflow для агентной разработки.

- [AGENTS.md](AGENTS.md) — глобальное правило: читать Markdown до работы с кодом и обновлять Markdown после работы с кодом.
- [promt_agent.md](promt_agent.md) — краткий рабочий дневник агента; обязателен к чтению перед работой и к обновлению после работы.
- [docs/agents/README.md](docs/agents/README.md) — индекс агентной документации.
- [docs/agents/WORKFLOW.md](docs/agents/WORKFLOW.md) — обязательный маршрут работы агента.
- [docs/agents/DOCS_MAP.md](docs/agents/DOCS_MAP.md) — карта документов по репозиторию.
- [docs/agents/services/README.md](docs/agents/services/README.md) — сервисные профили и обязательные документы для чтения.

---

## Быстрый старт

### Требования

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (запущен)
- .NET SDK ≥ 8.0 (для сборки .NET-сервисов без Docker)
- Python 3.12+ (для локального запуска аналитики без Docker)

### Linux / macOS

```bash
cd microservicestarter/

# Запуск всех сервисов
./start.sh

# Запуск всего backend-стека без admin
./start.sh all noadmin

# Запуск только отдельной online-head admin-ноды
./start.sh all onlyadmin
./start.sh all onlyadmin 10.44.0.1

# Запуск конкретного сервиса
./start.sh microservice_infra
./start.sh microservice_data
./start.sh microservice_admin
./start.sh microservice_analitic
./start.sh microservice_account
./start.sh microservice_gateway

# Остановка
./stop.sh                                # все сервисы
./stop.sh microservice_analitic clean    # остановить + удалить volumes и repo-local runtime data

# Перезапуск с git pull
./restart.sh
./restart.sh all noadmin
./restart.sh all onlyadmin
./restart.sh all onlyadmin 10.44.0.1
./restart.sh microservice_analitic

# Статус контейнеров
./status.sh
./status.sh microservice_analitic
```

### Windows (PowerShell)

```powershell
cd microservicestarter\

# Запуск всех сервисов
.\start.ps1

# Запуск всего backend-стека без admin
.\start.ps1 -Mode noadmin

# Запуск только отдельной online-head admin-ноды
.\start.ps1 -Mode onlyadmin
.\start.ps1 -Mode onlyadmin -BackendHost 10.44.0.1

# Запуск конкретного сервиса
.\start.ps1 -Service microservice_infra
.\start.ps1 -Service microservice_data
.\start.ps1 -Service microservice_admin
.\start.ps1 -Service microservice_analitic
.\start.ps1 -Service microservice_account
.\start.ps1 -Service microservice_gateway

# Остановка
.\stop.ps1
.\stop.ps1 -Service microservice_analitic -Mode clean  # остановить + удалить volumes и repo-local runtime data

# Перезапуск с git pull
.\restart.ps1
.\restart.ps1 -Mode noadmin
.\restart.ps1 -Mode onlyadmin
.\restart.ps1 -Mode onlyadmin -BackendHost 10.44.0.1
.\restart.ps1 -Service microservice_analitic

# Статус
.\status.ps1
.\status.ps1 -Service microservice_analitic
```

---

## Режимы запуска и перезапуска

| Режим       | Описание                                                                |
| ----------- | ----------------------------------------------------------------------- |
| `core`      | Запуск основного стека — **по умолчанию**                               |
| `noadmin`   | Запуск всего backend-стека, кроме `microservice_admin`                  |
| `onlyadmin` | Запуск только `microservice_admin` как отдельной online-head ноды       |
| `full`      | Core + планировщик переобучения (scheduler)                             |
| `scheduler` | Только scheduler (требует запущенного core)                             |
| `build`     | Пересборка образов без кеша + запуск                                    |
| `logs`      | Показать live-логи (только `start`)                                     |
| `api`       | Пересобрать и перезапустить только api-контейнер (только `restart`)     |
| `deps`      | Пересобрать base-образ + всё сверху (только `restart`)                  |
| `clean`     | Остановить + удалить volumes и repo-local runtime data (только `stop`)  |
| `prune`     | Остановить + удалить Docker-образы сервиса (только `stop`)              |

Для multi-service сценариев `start` и `restart` больше не выполняют все
compose-команды строго последовательно. Launcher сначала поднимает
`microservice_infra`, а затем запускает остальные выбранные сервисы
параллельно отдельными дочерними процессами. За счёт этого общий `build + up`
для полного стека заметно быстрее, но bootstrap общей сети и ingress-а
остаётся детерминированным.

---

## Переменные окружения

При **первом запуске** через `start.sh` / `start.ps1` launcher автоматически создаёт `.env` из `.env.example` в тех сервисах, где это поддерживается, и запрашивает обязательные секреты.

Если нужно подготовить окружение вручную, ориентируйся на `README.md` конкретного сервиса и его `.env.example`.

Для `onlyadmin`/online-head сценария ориентируйся дополнительно на
`microservice_admin/README.md`: remote admin использует namespace
`ONLINE_*` для Kafka bootstrap и внешних health endpoints.

Launcher также умеет принять один backend host/IP для `onlyadmin` и сам
заполнить derived `ONLINE_*` в `microservice_admin/.env`. Если аргумент не
передан, в интерактивном режиме он спросит backend host/IP и сохранит его как
`ONLINE_BACKEND_HOST`.

## Локальное хранение runtime-данных

Runtime-данные stateful Docker-сервисов по умолчанию хранятся в каталоге
репозитория `.runtime-data/`, а не в Docker named volumes:

- `.runtime-data/microservice_infra/redpanda`
- `.runtime-data/microservice_infra/minio`
- `.runtime-data/microservice_account/postgres`
- `.runtime-data/microservice_account/redis`
- `.runtime-data/microservice_data/postgres`
- `.runtime-data/microservice_analitic/redis`
- `.runtime-data/microservice_analitic/models`

`.runtime-data/` уже исключён из git через `.gitignore`, а
`microservicestarter stop ... clean` для этих сервисов удаляет и Docker
volumes, и соответствующие каталоги внутри `.runtime-data/`.

На Linux launcher (`start.sh` / `restart.sh`) дополнительно сам создаёт эти
каталоги перед `docker compose up` и нормализует права записи для bind mounts,
чтобы stateful контейнеры корректно стартовали на свежем сервере.

---

## Добавление нового микросервиса

1. Создайте папку сервиса в корне репозитория и сразу добавьте в неё как минимум `README.md`, `STRUCTURE.md`, `docker-compose.yml` и, при необходимости, `.env.example`.
2. Зарегистрируйте сервис в `microservicestarter/services.conf`:

   ```text
   my_new_service  my_new_service
   ```

3. Обновите [STRUCTURE.md](STRUCTURE.md), [docs/agents/DOCS_MAP.md](docs/agents/DOCS_MAP.md) и профиль в `docs/agents/services/`, чтобы новый сервис вошёл в docs-first маршрут чтения.
4. Готово — все команды `start/stop/restart/status` автоматически увидят новый сервис.
