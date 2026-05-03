# Agent Change Log

Журнал коротких записей о заметных изменениях, которые влияют на разработку, маршруты чтения документации и агентный workflow.

## 2026-05

### 2026-05-03

- Зафиксировано общее правило: `promt_agent.md` стал обязательным кратким дневником агента, который нужно читать перед работой и обновлять после работы.
- Зафиксировано архитектурное ограничение: `microservice_admin` не исполняет jobs внутри себя, а только отправляет команды и отображает состояние jobs владельцев-микросервисов.
- `microservice_data`: startup-path dataset jobs переведён в non-blocking режим (`Task.Yield` в Kafka consumer, schema bootstrap/recovery в `DatasetJobRunner`), а старые broken queued jobs теперь мягко переводятся в terminal state.
- `microservice_admin`: ingest ALL UI переведён с псевдопрогресса по таймфреймам на slot-based remote-jobs view (2 execution slots, queue, stalled-state, recent results).
- `microservice_admin`: локальный ingest busy-state/lock теперь удерживается до terminal remote job вместо мгновенного освобождения после `JOBS_START`, а `succeeded` с `completed=0` показывается как нормальный no-op, не как подозрительный нулевой результат.
- `microservice_admin`: `DatasetJobsPanel` косметически возвращён в штатный dark/card стиль панели вместо светлого инородного блока; структура и информативность сохранены.
- `microservice_admin` + `microservice_infra`: Dataset CSV/ZIP export больше не зависит от raw browser redirect на `localhost/minio:9000` в proxy-контуре: admin route нормализует signed bucket URL на текущий внешний origin, страница даёт явную ошибку про invalid download path, а nginx теперь проксирует `/modelline-blobs/*` в MinIO без изменения signed path/query.
- `microservice_data`: повторно подтверждено и задокументировано, что export pipeline уже stream-only для single CSV и ALL ZIP (`Pipe` + PostgreSQL COPY + multipart upload в MinIO); исправлен устаревший комментарий про «ZIP in memory», чтобы документация совпадала с реальным runtime.

### 2026-05-02

- Создан базовый docs-first каркас для агентов: `AGENTS.md`, `.github/instructions/markdown-governance.instructions.md`, каталог `docs/agents/` и сервисные профили.
- Зафиксировано обязательное правило: до работы с кодом читать Markdown-опоры, после работы с кодом обновлять Markdown-опоры.
- Добавлены недостающие `STRUCTURE.md` для `microservice_account`, `microservice_gateway` и `microservicestarter`, чтобы у всех основных сервисов была полная пара README + STRUCTURE.
- Корневые и сервисные README/STRUCTURE выровнены под единый агентский маршрут чтения.