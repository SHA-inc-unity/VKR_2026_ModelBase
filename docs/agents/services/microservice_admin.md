# microservice_admin

## Что это

Admin UI на Next.js для Kafka-driven операций платформы. Ownership ограничен UI, proxy и наблюдением за состоянием. Фактическое исполнение jobs должно происходить только в доменных микросервисах, не внутри admin.

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