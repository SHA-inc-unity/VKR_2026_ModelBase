# promt_agent

Краткий рабочий дневник агента по репозиторию ModelLine.

## Правила ведения

- Читать этот файл перед началом любой работы после базовых workflow-документов.
- После завершения работы добавлять короткую запись: что выяснили, что сделали, что осталось.
- Писать кратко и по делу, без длинной исторической ленты.

## Текущий контекст

### 2026-05-18

- Split deployment стандартизирован на один маршрут: backend HTTPS facade на `:8443` через `ADMIN_BACKEND_BASE_URL` и `ADMIN_SHARED_TOKEN`.
- `microservicestarter` в `onlyadmin` сам собирает `ONLINE_*`, `ADMIN_BACKEND_BASE_URL` и `ADMIN_BACKEND_SHARED_TOKEN`; в `noadmin` собирает `PUBLIC_DOWNLOAD_BASE_URL`, `ADMIN_SHARED_TOKEN` и `ADMIN_BACKEND_PORT`.
- Старый split-transport код, compose-сервисы и отдельные markdown-гайды удалены из активной схемы.
- Актуальные операционные документы: root `README.md`, `STRUCTURE.md`, `microservicestarter/README.md`, `microservice_admin/README.md`, `microservice_infra/README.md`.
- `microservice_admin/src/app/api/health/route.ts`: split-mode health response снова совпадает с `InfraHealthResponse`; `npm run build` для admin проходит.
