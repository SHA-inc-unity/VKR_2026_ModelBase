# promt_agent

Краткий рабочий дневник агента по репозиторию ModelLine.

## Правила ведения

- Читать этот файл перед началом любой работы после базовых workflow-документов.
- После завершения работы добавлять короткую запись: что выяснили, что сделали, что осталось.
- Писать кратко и по делу, без диффов и без длинных рассуждений.

## Текущий контекст

### 2026-05-03

- Синхронизированы `AGENTS.md`, `docs/agents/*`, корневые документы и `microservice_admin` docs под два правила: обязательный дневник агента и запрет на исполнение jobs внутри admin.
- Перепроверены правила составления промтов в памяти: один общий промт, абстрактный, без файлов/строк/классов, с оценкой объёма перед ним и с обязательным финалом про обновление README/STRUCTURE.
- Подготовлен единый промт на мягкое исправление dataset jobs и улучшение ingest progress с разделением ответственности между `microservice_data` и `microservice_admin`.
- Зафиксировано правило ownership: `microservice_admin` не исполняет jobs и не держит job-runner'ы; он только управляет и наблюдает jobs других сервисов.
- По dataset jobs подтверждён текущий основной runtime-сбой: у `microservice_data` jobs остаются в `queued`, потому что startup, вероятно, блокируется в `KafkaConsumerService`, из-за чего не стартует `DatasetJobRunner` и не поднимается `/health`.
- Подтверждено по коду: ingest в `microservice_data` задуман как 2 параллельных jobs, но в текущем runtime фактически выполняется 0.
- Следующий артефакт для пользователя: мягкий план исправления без жёсткой переделки архитектуры и с отдельным улучшением ingest progress UI.
- Исправлен backend lifecycle: `KafkaConsumerService` больше не блокирует host startup, `Program.cs` не ждёт jobs schema до `app.Run()`, а `DatasetJobRunner` сам ретраит schema bootstrap, подхватывает очередь FIFO и мягко завершает старые invalid queued jobs.
- Исправлен admin ingest ALL UI: progress построен вокруг remote dataset jobs (2 execution slots, queue, stalled-state, recent done/error), без ложного running и без обнуления coverage.
- Дополнительно отполирован ingest UX в `microservice_admin`: локальный `loading/busy` и page-level lock теперь живут до terminal remote job, а успешный ingest без новых строк показывается как штатный no-op (`без новых строк` / `дозагрузка не потребовалась`).
- Для этой доводки дополнительных кодовых изменений в `microservice_data` не потребовалось: текущего `completed=0` в terminal event уже достаточно для честной no-op семантики на UI.
- `DatasetJobsPanel` на странице Dataset переведён с inline light-стилей на штатный тёмный card-вид admin-панели; визуальный белый фрагмент убран без изменения структуры, кнопок и состава данных.
- По CSV-export подтверждён runtime-конфиг-сбой: живой `presigned_url` сейчас указывает на `http://localhost:9000/...`, потому что `MINIO_PUBLIC_URL` в data-service реально выставлен в `localhost:9000`; browser уводится на сырой MinIO-порт вместо нормального внешнего download path.
- По исходному коду export-пайплайн уже потоковый (Admin не тащит байты через себя, DataService пишет CSV/ZIP в MinIO через pipe + multipart upload), поэтому следующий prompt должен чинить прежде всего внешний download path и отдельно перепроверять, нет ли скрытой материализации/ретраев, которые дают всплеск памяти на больших выгрузках.
- CSV/ZIP export мягко доведён без смены архитектуры: `microservice_admin` нормализует raw `localhost/minio:9000` presigned URL в текущий внешний origin для `/modelline-blobs/*`, страница до клика блокирует явно внутренний/non-browser-reachable download path понятным сообщением, а `microservice_infra/nginx` теперь проксирует signed object downloads на bucket root path.
- Перепроверка памяти завершена: dataset export в `microservice_data` уже был stream-only (`Pipe` + `ExportCsvToStreamAsync` + `PutStreamAsync` + multipart upload), поэтому кодово выровнен только вводящий в заблуждение комментарий про ZIP-in-memory; полной материализации CSV/ZIP в runtime по текущему path нет.
- Подтверждён лучший следующий шаг для локального download path: оставить вход пользователя на `:8501`, но сделать этот порт точкой входа reverse proxy, чтобы `/admin/*` и `/modelline-blobs/*` жили на одном origin, а большие CSV/ZIP по-прежнему шли напрямую из object storage, не через admin-process.
- Подготовлен единый абстрактный промт на реализацию этой схемы без raw `:9000` в браузере и без прокачки больших файлов через Next.js/admin.
