# microservice_admin

## Что это

Admin UI на Next.js для Kafka-driven операций платформы. Ownership ограничен UI, proxy и наблюдением за состоянием. Фактическое исполнение jobs должно происходить только в доменных микросервисах, не внутри admin.

Admin auth: `/login` вызывает Account Service login, принимает только роль `admin`, хранит access/refresh tokens в httpOnly cookies и пересылает admin JWT в gateway facade при split deployment. Поле логина принимает username или email; при пустом `AdminBootstrap:*` первый старт Account Service создаёт дефолтного admin `admin/admin`. `src/middleware.ts` скрывает панель до login и умеет silently восстановить access token по refresh token, поэтому cached admin-session обычно переживает reload/revisit до истечения refresh token. Общий статический ключ между admin-host и backend-host не используется.

## Что читать перед кодом

- [../../../microservice_admin/README.md](../../../microservice_admin/README.md)
- [../../../microservice_admin/STRUCTURE.md](../../../microservice_admin/STRUCTURE.md)
- [../WORKFLOW.md](../WORKFLOW.md)

## Что обновлять после кода

- `microservice_admin/README.md`
- `microservice_admin/STRUCTURE.md`
- [../CHANGE_LOG.md](../CHANGE_LOG.md)

## Когда обязательно обновлять Markdown

- изменения UI-flow
- изменения Kafka topic usage, SSE, cache, api proxy
- изменения маршрутов, экранов, диаграмм, прогресса, job-отображения
- изменения admin login/session, middleware и backend facade auth
