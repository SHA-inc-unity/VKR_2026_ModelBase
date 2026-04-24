# microservicestarter

Общий менеджер для запуска, остановки и обновления микросервисов ModelLine.

## Реестр сервисов

`services.conf` — текстовый файл, по одному сервису на строку:
```
<service_name>  <path_from_repo_root>
```

Текущие сервисы:
- `microservice_analitic` — аналитика и ML-модели

## Быстрый старт

**Linux/macOS:**
```bash
./start.sh                        # запустить все сервисы
./restart.sh                      # git pull + перезапустить все
./stop.sh                         # остановить все
./status.sh                       # посмотреть состояние
./update.sh                       # только git pull
```

**Windows (PowerShell):**
```powershell
.\start.ps1                       # запустить все сервисы
.\restart.ps1                     # git pull + перезапустить все
.\stop.ps1                        # остановить все
.\status.ps1                      # посмотреть состояние
.\update.ps1                      # только git pull
```

Подробная документация и таблица режимов — в корневом [README.md](../README.md).

## Режимы restart.ps1

| Режим | Команда | Поведение |
|-------|---------|-----------|
| `core` (default) | `.\restart.ps1` | `docker compose up -d --build` — атомарная сборка + запуск |
| `api` | `.\restart.ps1 api` | `docker compose up -d --no-deps --build api` — только api-сервис |
| `full` | `.\restart.ps1 full` | `docker compose --profile scheduler up -d --build` — со scheduler |
| `deps` | `.\restart.ps1 deps` | двухшаговый: сначала `build --no-cache base`, затем `up -d` |
| `postgres` | `.\restart.ps1 postgres` | перезапуск postgres |
| `redis` | `.\restart.ps1 redis` | перезапуск redis |

> **Примечание:** в режимах `core`, `api`, `full` используется атомарная команда `up --build`.
> Отдельный вызов `docker compose build` применяется только в режиме `deps` (для пересборки base-образа без кэша).
