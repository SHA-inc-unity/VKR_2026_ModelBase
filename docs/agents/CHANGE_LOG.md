# Agent Change Log

Журнал коротких записей о заметных изменениях, которые влияют на разработку, маршруты чтения документации и агентный workflow.

## 2026-05

### 2026-05-03

- `microservicestarter/README.md`: после дополнительной проверки Markdown исправлены неверные PowerShell-примеры для `restart.ps1` (`api/full/deps/postgres/redis` теперь документированы с явным `-Mode` и target-service), а описание параллельного запуска переформулировано под реальное multi-service поведение launcher-а.
- `microservicestarter`: исправлен PowerShell parallel fan-out для `start.ps1` и `restart.ps1`. В этом host-е `Start-Process(...).ExitCode` возвращал пустое значение даже при успешном дочернем завершении, из-за чего launcher ложно падал после успешного `docker compose`. Parent-side проверка переписана на явный `ResultFile` handoff (`OK` / `FAIL`) между дочерним и родительским launcher-процессом; живые прогоны `./restart` и `./start` на Windows подтверждены.
- `microservicestarter`: `start`/`restart` для multi-service сценариев ускорены параллельным fan-out. Launcher сначала поднимает `microservice_infra`, затем запускает/перезапускает остальные выбранные сервисы параллельно отдельными дочерними процессами; для `restart` `git pull` по-прежнему выполняется один раз до этого fan-out.
- `microservicestarter` + `microservice_admin`: добавлены split-deployment режимы `noadmin` и `onlyadmin`. Launcher умеет запускать backend-стек без локального admin и отдельно поднимать `microservice_admin` как online-head ноду; для online-head введён compose-service `admin-online` и namespace переменных `ONLINE_*`.
- Зафиксировано общее правило: `promt_agent.md` стал обязательным кратким дневником агента, который нужно читать перед работой и обновлять после работы.
- Зафиксировано архитектурное ограничение: `microservice_admin` не исполняет jobs внутри себя, а только отправляет команды и отображает состояние jobs владельцев-микросервисов.
- `microservice_data`: startup-path dataset jobs переведён в non-blocking режим (`Task.Yield` в Kafka consumer, schema bootstrap/recovery в `DatasetJobRunner`), а старые broken queued jobs теперь мягко переводятся в terminal state.
- `microservice_admin`: ingest ALL UI переведён с псевдопрогресса по таймфреймам на slot-based remote-jobs view (2 execution slots, queue, stalled-state, recent results).
- `microservice_admin`: локальный ingest busy-state/lock теперь удерживается до terminal remote job вместо мгновенного освобождения после `JOBS_START`, а `succeeded` с `completed=0` показывается как нормальный no-op, не как подозрительный нулевой результат.
- `microservice_admin`: `DatasetJobsPanel` косметически возвращён в штатный dark/card стиль панели вместо светлого инородного блока; структура и информативность сохранены.
- `microservice_admin` + `microservice_infra`: Dataset CSV/ZIP export больше не зависит от raw browser redirect на `localhost/minio:9000` в proxy-контуре: admin route нормализует signed bucket URL на текущий внешний origin, страница даёт явную ошибку про invalid download path, а nginx теперь проксирует `/modelline-blobs/*` в MinIO без изменения signed path/query.
- `microservice_data`: повторно подтверждено и задокументировано, что export pipeline уже stream-only для single CSV и ALL ZIP (`Pipe` + PostgreSQL COPY + multipart upload в MinIO); исправлен устаревший комментарий про «ZIP in memory», чтобы документация совпадала с реальным runtime.
- Платформа: единый внешний вход переехал на `microservice_infra/nginx` (host-порт `8501`). `microservice_admin` снят с прямой публикации на хост и теперь виден только как `admin:3000` внутри `modelline_net`. Browser-вход — `http://localhost:8501/admin/`, dataset CSV/ZIP скачиваются с того же origin через `/modelline-blobs/*` без прокачки байтов через admin runtime.
- `microservice_data`: переменная `MINIO_PUBLIC_URL` заменена на `PUBLIC_DOWNLOAD_BASE_URL` (default `http://localhost:8501`) для browser-bound presigned URL. Server-to-server `cmd.data.dataset.export_full` (потребляется microservice_analitic из той же docker-сети) подписывается на внутренний `http://minio:9000` — аналитика не зависит от host-only proxy URL.
- `microservice_admin`: `/api/export/csv` и `/download` упрощены — `normalizePresignedDownloadUrl` и `explainExportDownloadPath` убраны полностью; admin не нормализует URL, а только проксирует `presigned_url` от data-сервиса. Никакого raw `localhost/minio:9000` legacy fallback'а не осталось.
- `microservicestarter`: интерактивный prompt про nginx-проброс убран из `start.ps1`/`restart.ps1`. Обычный `start` поднимает infra-nginx на 8501 без дополнительных профилей и без интерактива.

### 2026-05-02

- Создан базовый docs-first каркас для агентов: `AGENTS.md`, `.github/instructions/markdown-governance.instructions.md`, каталог `docs/agents/` и сервисные профили.
- Зафиксировано обязательное правило: до работы с кодом читать Markdown-опоры, после работы с кодом обновлять Markdown-опоры.
- Добавлены недостающие `STRUCTURE.md` для `microservice_account`, `microservice_gateway` и `microservicestarter`, чтобы у всех основных сервисов была полная пара README + STRUCTURE.
- Корневые и сервисные README/STRUCTURE выровнены под единый агентский маршрут чтения.
