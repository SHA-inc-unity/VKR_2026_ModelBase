# ModelLine — Микросервисная архитектура

Монорепозиторий из трёх независимых микросервисов, управляемых единым лаунчером **microservicestarter**.

---

## Сервисы

| Сервис | Технологии | Порты | Описание |
|--------|-----------|-------|----------|
| [microservice_analitic](microservice_analitic/README.md) | Python 3.12, CatBoost, FastAPI, Streamlit, PostgreSQL, Redis | API: `8000`, UI: `8501` | ML-сервис: обучение, прогнозы, аналитика рынка |
| [microservice_account](microservice_account/README.md) | .NET 8, ASP.NET Core, PostgreSQL, Redis | `5010` | Сервис аутентификации и управления аккаунтами (Clean Architecture) |
| [microservice_gateway](microservice_gateway/README.md) | .NET 8, ASP.NET Core | `5020` | Mobile BFF Gateway — маршрутизация и агрегация запросов |

---

## Структура репозитория

```
/
├── microservicestarter/          # Общий менеджер для всех микросервисов
│   ├── services.conf             # Реестр сервисов
│   ├── start.sh / start.ps1     # Запуск
│   ├── stop.sh / stop.ps1       # Остановка
│   ├── restart.sh / restart.ps1 # git pull + перезапуск
│   ├── update.sh / update.ps1   # Только git pull (без рестарта)
│   └── status.sh / status.ps1   # Состояние контейнеров
│
├── microservice_analitic/        # Сервис аналитики и ML-моделей
├── microservice_account/         # Сервис аккаунтов и авторизации
├── microservice_gateway/         # BFF Gateway
└── README.md                     # Этот файл
```

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

# Запуск конкретного сервиса
./start.sh microservice_analitic
./start.sh microservice_account
./start.sh microservice_gateway

# Остановка
./stop.sh                                # все сервисы
./stop.sh microservice_analitic clean    # остановить + удалить volumes (СБРОС БД!)

# Перезапуск с git pull
./restart.sh
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

# Запуск конкретного сервиса
.\start.ps1 -Service microservice_analitic
.\start.ps1 -Service microservice_account
.\start.ps1 -Service microservice_gateway

# Остановка
.\stop.ps1
.\stop.ps1 -Service microservice_analitic -Mode clean  # остановить + удалить volumes (СБРОС БД!)

# Перезапуск с git pull
.\restart.ps1
.\restart.ps1 -Service microservice_analitic

# Статус
.\status.ps1
.\status.ps1 -Service microservice_analitic
```

---

## Режимы запуска и перезапуска

| Режим       | Описание                                                           |
|-------------|--------------------------------------------------------------------|
| `core`      | Запуск основного стека — **по умолчанию**                          |
| `full`      | Core + планировщик переобучения (scheduler)                        |
| `scheduler` | Только scheduler (требует запущенного core)                        |
| `build`     | Пересборка образов без кеша + запуск                               |
| `logs`      | Показать live-логи (только `start`)                                |
| `api`       | Пересобрать и перезапустить только api-контейнер (только `restart`)|
| `streamlit` | Пересобрать и перезапустить только streamlit (только `restart`)    |
| `deps`      | Пересобрать base-образ + всё сверху (только `restart`)             |
| `clean`     | Остановить + удалить volumes (только `stop`) — **СБРОС БД!**       |
| `prune`     | Остановить + удалить Docker-образы сервиса (только `stop`)         |

---

## Переменные окружения

При **первом запуске** через `start.sh` / `start.ps1` скрипт автоматически создаёт `.env` из `.env.example` и запрашивает пароль PostgreSQL в интерактивном режиме.

Создать `.env` вручную:

```bash
cp microservice_analitic/.env.example microservice_analitic/.env
cp microservice_account/.env.example microservice_account/.env
cp microservice_gateway/.env.example microservice_gateway/.env
```

Подробности по каждому сервису — в README соответствующей папки.

---

## Добавление нового микросервиса

1. Создайте папку с `docker-compose.yml` и `.env.example` в корне репозитория.
2. Зарегистрируйте сервис в `microservicestarter/services.conf`:
   ```
   my_new_service  my_new_service
   ```
3. Готово — все команды `start/stop/restart/status` автоматически увидят новый сервис.
