# AGENTS.md

Этот репозиторий ведётся в режиме docs-first для всех агентных и полуавтоматических изменений.

## Обязательное правило

Перед любой работой с кодом агент обязан прочитать Markdown-документы, относящиеся к задаче.
После завершения работы агент обязан синхронизировать Markdown-документы с фактическими изменениями.

## Обязательный порядок чтения перед кодом

1. [README.md](README.md)
2. [STRUCTURE.md](STRUCTURE.md)
3. [docs/agents/README.md](docs/agents/README.md)
4. [docs/agents/WORKFLOW.md](docs/agents/WORKFLOW.md)
5. [docs/agents/DOCS_MAP.md](docs/agents/DOCS_MAP.md)
6. [promt_agent.md](promt_agent.md)
7. README и, если существует, STRUCTURE затронутого сервиса
8. Профиль затронутого сервиса в [docs/agents/services/README.md](docs/agents/services/README.md)

## Дополнительные проектные правила

- `promt_agent.md` — обязательный краткий рабочий дневник агента. Его нужно читать перед работой и обновлять после работы краткой записью по делу: что выяснили, что сделали, что осталось.
- `microservice_admin` не исполняет фоновые jobs внутри себя и не является job-runner'ом. Admin только отправляет команды, читает статусы и показывает jobs, которые фактически выполняются во владельцах-микросервисах.

## Обязательные действия после кодовой работы

1. Обновить README.md и STRUCTURE.md затронутого сервиса, если изменились поведение, контракты, структура, процессы или ограничения.
2. Обновить профиль сервиса в docs/agents/services, если изменился агентский маршрут чтения, ownership, входные точки или обязательные документы.
3. Обновить [promt_agent.md](promt_agent.md) краткой записью о выполненной работе.
4. Обновить [docs/agents/CHANGE_LOG.md](docs/agents/CHANGE_LOG.md) краткой записью о выполненной работе.
5. Если изменение меняет общую архитектуру репозитория или общие правила разработки, обновить [README.md](README.md), [STRUCTURE.md](STRUCTURE.md), [docs/agents/WORKFLOW.md](docs/agents/WORKFLOW.md) и [docs/agents/DOCS_MAP.md](docs/agents/DOCS_MAP.md).

## Правило завершения задачи

Задача с кодом не считается завершённой, пока Markdown-документы не приведены в актуальное состояние.

## Охват

Правило распространяется на все сервисы и общие каталоги репозитория:

- microservice_admin
- microservice_data
- microservice_analitic
- microservice_account
- microservice_gateway
- microservice_infra
- microservicestarter
- shared
