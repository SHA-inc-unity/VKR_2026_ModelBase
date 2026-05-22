# microservice_data

## Что это

Data-сервис на .NET, владелец датасета, Kafka-команд и фоновых jobs.

Отдельно владеет dedicated runtime market watcher-а: live overlay больше не живёт
в `dataset_jobs`, а поднимается как hosted service внутри data-service и
управляется через Kafka topics `cmd.data.market_watcher.{status,set_enabled,rows,logs}`.

## Что читать перед кодом

- [../../../microservice_data/README.md](../../../microservice_data/README.md)
- [../../../microservice_data/STRUCTURE.md](../../../microservice_data/STRUCTURE.md)
- [../../../microservice_data/EXCHANGE_APIS.md](../../../microservice_data/EXCHANGE_APIS.md)
- [../WORKFLOW.md](../WORKFLOW.md)

## Что обновлять после кода

- `microservice_data/README.md`
- `microservice_data/STRUCTURE.md`
- `microservice_data/EXCHANGE_APIS.md`
- [../CHANGE_LOG.md](../CHANGE_LOG.md)

## Когда обязательно обновлять Markdown

- изменения Kafka handlers и topic contracts
- изменения dataset jobs, ingest pipeline, retry, timeout, concurrency
- изменения runtime/control-plane market watcher-а и `market_watch_live`
- изменения структуры таблиц, схемы, MinIO/export и health/readiness
