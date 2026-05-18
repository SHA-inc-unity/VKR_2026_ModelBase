# Agent Change Log

Журнал коротких записей о заметных изменениях, которые влияют на разработку, маршруты чтения документации и агентный workflow.

## 2026-05

### 2026-05-18

- `microservicestarter/start.sh`, `microservicestarter/restart.sh`, `microservicestarter/start.ps1`, `microservicestarter/restart.ps1`: launcher закреплён за одним split deployment path через backend HTTPS facade и сам собирает недостающие `ONLINE_*`, `ADMIN_BACKEND_BASE_URL`, `ADMIN_BACKEND_SHARED_TOKEN`, `PUBLIC_DOWNLOAD_BASE_URL`, `ADMIN_SHARED_TOKEN`, `ADMIN_BACKEND_PORT`.
- Те же launcher-скрипты: в `noadmin` отсутствующий `ADMIN_SHARED_TOKEN` теперь генерируется автоматически на backend-host и сохраняется в `microservice_gateway/.env`; в `onlyadmin` автогенерация для `ADMIN_BACKEND_SHARED_TOKEN` убрана, launcher просит именно backend-generated token.
- `microservicestarter/start.sh`, `microservicestarter/restart.sh`: bash-ветка parallel start/restart теперь включает список конкретных упавших сервисов в итоговой ошибке, а не только общий failure summary.
- `microservicestarter/start.sh`, `microservicestarter/restart.sh`, `microservicestarter/start.ps1`, `microservicestarter/restart.ps1`: launcher теперь автоматически восстанавливает отсутствующий `.env` из `.env.example` во время запуска/рестарта и больше не делает конкурентный `docker image prune` в child-процессах parallel fan-out.
- `microservice_analitic/docker-compose.yml`: internal Redis больше не публикуется на host `6379`, чтобы backend auto-deploy не падал на port conflict; launcher cleanup дополнительно защищён межпроцессным lock-ом вокруг `docker image prune`.
- `microservice_admin/docker-compose.yml`, `microservice_infra/docker-compose.yml`, `microservice_admin/.env.example`: удалены legacy split-transport сервисы и переменные. Split deployment больше не документируется и не поддерживается через join-token transport.
- `README.md`, `STRUCTURE.md`, `microservicestarter/README.md`, `microservicestarter/STRUCTURE.md`, `microservice_admin/README.md`, `microservice_admin/STRUCTURE.md`, `microservice_infra/README.md`, `microservice_infra/STRUCTURE.md`: документация синхронизирована под один официальный маршрут backend `:8443` + `ADMIN_SHARED_TOKEN`.
- Два obsolete split-transport guide-файла удалены из `microservice_infra`.
- `microservice_admin/src/app/api/health/route.ts`: исправлен split-mode response `/api/health` для production typecheck. Route снова возвращает полный `KafkaBrokerHealth` с `bootstrapServers` и согласованными `online/offline` статусами; `npm run build` в `microservice_admin` проходит.
