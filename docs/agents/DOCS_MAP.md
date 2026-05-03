# Docs Map

## Корневые документы

| Документ | Роль | Когда читать | Когда обновлять |
| --- | --- | --- | --- |
| [../../README.md](../../README.md) | Главный вход в репозиторий | Перед любой многосервисной задачей | При изменении состава репозитория, общих процессов и входных сценариев |
| [../../STRUCTURE.md](../../STRUCTURE.md) | Карта монорепозитория | Перед любой задачей, затрагивающей структуру | При изменении каталогов, сервисов и общей архитектуры |
| [../../AGENTS.md](../../AGENTS.md) | Общие правила агентной работы | Перед любой работой с кодом | При изменении agent workflow |
| [../../promt_agent.md](../../promt_agent.md) | Краткий рабочий дневник агента | Перед любой работой с кодом после чтения workflow | После каждой завершённой рабочей сессии |

## Агентные документы

| Документ | Роль | Когда читать | Когда обновлять |
| --- | --- | --- | --- |
| [README.md](README.md) | Индекс агентной документации | Перед началом работы | При изменении состава агентных опор |
| [WORKFLOW.md](WORKFLOW.md) | Порядок действий до и после кода | Перед началом работы | При изменении правил работы агента |
| [CHANGE_LOG.md](CHANGE_LOG.md) | Журнал изменений | После ознакомления с workflow | После каждой заметной кодовой задачи |
| [services/README.md](services/README.md) | Индекс сервисных профилей | Перед входом в конкретный сервис | При изменении состава сервисных профилей |

## Сервисные документы

| Сервис | Обязательные документы перед кодом | Обязательные документы после кода |
| --- | --- | --- |
| microservice_admin | `microservice_admin/README.md`, `microservice_admin/STRUCTURE.md`, `docs/agents/services/microservice_admin.md` | те же документы + `docs/agents/CHANGE_LOG.md` |
| microservice_data | `microservice_data/README.md`, `microservice_data/STRUCTURE.md`, `docs/agents/services/microservice_data.md` | те же документы + `docs/agents/CHANGE_LOG.md` |
| microservice_analitic | `microservice_analitic/README.md`, `microservice_analitic/STRUCTURE.md`, `docs/agents/services/microservice_analitic.md` | те же документы + `docs/agents/CHANGE_LOG.md` |
| microservice_account | `microservice_account/README.md`, `microservice_account/STRUCTURE.md`, `docs/agents/services/microservice_account.md` | те же документы + `docs/agents/CHANGE_LOG.md` |
| microservice_gateway | `microservice_gateway/README.md`, `microservice_gateway/STRUCTURE.md`, `docs/agents/services/microservice_gateway.md` | те же документы + `docs/agents/CHANGE_LOG.md` |
| microservice_infra | `microservice_infra/README.md`, `microservice_infra/STRUCTURE.md`, профиль сервиса в `docs/agents/services/` | те же документы + `docs/agents/CHANGE_LOG.md` |
| microservicestarter | `microservicestarter/README.md`, `microservicestarter/STRUCTURE.md`, `docs/agents/services/microservicestarter.md` | те же документы + `docs/agents/CHANGE_LOG.md` |
| shared | `shared/README.md`, `shared/STRUCTURE.md`, профиль сервиса в `docs/agents/services/` | те же документы + `docs/agents/CHANGE_LOG.md` |

## Правило синхронизации

Если затронут код, но не затронут ни один Markdown-документ, работа считается незавершённой.
