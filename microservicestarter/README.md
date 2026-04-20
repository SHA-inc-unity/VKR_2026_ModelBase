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
