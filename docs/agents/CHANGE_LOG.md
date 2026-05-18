# Agent Change Log

Журнал коротких записей о заметных изменениях, которые влияют на разработку, маршруты чтения документации и агентный workflow.

## 2026-05

### 2026-05-18

- `microservicestarter/start.sh`, `microservicestarter/restart.sh`, `microservicestarter/start.ps1`, `microservicestarter/restart.ps1`: launcher закреплён за одним split deployment path через backend HTTPS facade и сам собирает недостающие `ONLINE_*`, `ADMIN_BACKEND_BASE_URL`, `ADMIN_BACKEND_SHARED_TOKEN`, `PUBLIC_DOWNLOAD_BASE_URL`, `ADMIN_SHARED_TOKEN`, `ADMIN_BACKEND_PORT`.
- `microservice_admin/docker-compose.yml`, `microservice_infra/docker-compose.yml`, `microservice_admin/.env.example`: удалены legacy split-transport сервисы и переменные. Split deployment больше не документируется и не поддерживается через join-token transport.
- `README.md`, `STRUCTURE.md`, `microservicestarter/README.md`, `microservicestarter/STRUCTURE.md`, `microservice_admin/README.md`, `microservice_admin/STRUCTURE.md`, `microservice_infra/README.md`, `microservice_infra/STRUCTURE.md`: документация синхронизирована под один официальный маршрут backend `:8443` + `ADMIN_SHARED_TOKEN`.
- Два obsolete split-transport guide-файла удалены из `microservice_infra`.
- `microservice_admin/src/app/api/health/route.ts`: исправлен split-mode response `/api/health` для production typecheck. Route снова возвращает полный `KafkaBrokerHealth` с `bootstrapServers` и согласованными `online/offline` статусами; `npm run build` в `microservice_admin` проходит.
