# microservice_account

## Что это

Сервис аккаунтов и авторизации на .NET.

Роли identity model: `guest`, `user`, `admin`. Public registration создаёт только `user`; login-only `admin` создаётся/promote-ится через bootstrap config. Auth responses возвращают UID/accountType/roles, но UID не является auth proof.

## Что читать перед кодом

- [../../../microservice_account/README.md](../../../microservice_account/README.md)
- [../../../microservice_account/STRUCTURE.md](../../../microservice_account/STRUCTURE.md)
- [../WORKFLOW.md](../WORKFLOW.md)

## Что обновлять после кода

- `microservice_account/README.md`
- `microservice_account/STRUCTURE.md`
- [../CHANGE_LOG.md](../CHANGE_LOG.md)

## Когда обязательно обновлять Markdown

- изменения auth-flow, JWT, UID/accountType response, roles, endpoints и deployment-конфигурации
