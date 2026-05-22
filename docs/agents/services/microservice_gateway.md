# microservice_gateway

## Что это

Mobile BFF gateway для внешних HTTP-запросов.

`/api/admin/*` — server-to-server admin facade, защищённый Account Service JWT с ролью `admin`; legacy static-key auth не используется.

## Что читать перед кодом

- [../../../microservice_gateway/README.md](../../../microservice_gateway/README.md)
- [../../../microservice_gateway/API.md](../../../microservice_gateway/API.md)
- [../../../microservice_gateway/STRUCTURE.md](../../../microservice_gateway/STRUCTURE.md)
- [../WORKFLOW.md](../WORKFLOW.md)

## Что обновлять после кода

- `microservice_gateway/README.md`
- `microservice_gateway/API.md`
- `microservice_gateway/STRUCTURE.md`
- [../CHANGE_LOG.md](../CHANGE_LOG.md)

## Когда обязательно обновлять Markdown

- изменения маршрутизации, downstream integration, auth passthrough/admin-role authorization, config и health-flow
