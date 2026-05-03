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
./restart.sh                      # git pull + перезапустить все
./restart.sh all noadmin          # git pull + всё, кроме admin
./restart.sh all onlyadmin        # git pull + только online-head admin
./stop.sh                         # остановить все
./status.sh                       # посмотреть состояние
./update.sh                       # только git pull
```

**Windows (PowerShell):**

```powershell
.\start.ps1                       # запустить все сервисы
.\start.ps1 -Mode noadmin         # запустить всё, кроме admin
.\start.ps1 -Mode onlyadmin       # запустить только online-head admin
.\restart.ps1                     # git pull + перезапустить все
.\restart.ps1 -Mode noadmin       # git pull + всё, кроме admin
.\restart.ps1 -Mode onlyadmin     # git pull + только online-head admin
.\stop.ps1                        # остановить все
.\status.ps1                      # посмотреть состояние
.\update.ps1                      # только git pull
```

Подробная документация и таблица режимов — в корневом [README.md](../README.md).

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
публикует свой `8501:3000` напрямую и читает внешние адреса из namespace
`ONLINE_*` (`ONLINE_KAFKA_BOOTSTRAP_SERVERS`, `ONLINE_REDPANDA_ADMIN_URL`,
`ONLINE_ACCOUNT_URL`, `ONLINE_GATEWAY_URL`, `ONLINE_MINIO_URL`,
`ONLINE_REDIS_URL`).

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
режим `onlyadmin` поднимает отдельный `admin-online` и публикует `8501:3000`
на своей машине.
