# microservice_data

## Что это

Data-сервис на .NET, владелец датасета, Kafka-команд и фоновых jobs.

## Что читать перед кодом

- [../../../microservice_data/README.md](../../../microservice_data/README.md)
- [../../../microservice_data/STRUCTURE.md](../../../microservice_data/STRUCTURE.md)
- [../WORKFLOW.md](../WORKFLOW.md)

## Что обновлять после кода

- `microservice_data/README.md`
- `microservice_data/STRUCTURE.md`
- [../CHANGE_LOG.md](../CHANGE_LOG.md)

## Когда обязательно обновлять Markdown

- изменения Kafka handlers и topic contracts
- изменения dataset jobs, ingest pipeline, retry, timeout, concurrency
- изменения структуры таблиц, схемы, MinIO/export и health/readiness