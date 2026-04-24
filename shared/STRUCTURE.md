# shared — Структура

> Обновляй этот файл при добавлении новых схем, топиков или утилит.

---

## Корень

| Файл | Описание |
|------|----------|
| `pyproject.toml` | Пакет `modelline-shared 0.1.0`. Зависимости: `pydantic>=2.5`, `aiokafka>=0.11`. `pip install -e ./shared` |

---

## modelline_shared/

### schemas.py

| Класс | Описание |
|-------|----------|
| `HealthResponse` | `status`, `service`, `version` — универсальный health-ответ для всех сервисов |

### messaging/

#### topics.py

Все Kafka-топики как строковые константы. Зеркалится в `microservice_admin/src/lib/topics.ts`.

| Константа | Значение | Тип |
|-----------|---------|-----|
| `CMD_DATA_HEALTH` | `cmd.data.health` | req/reply |
| `CMD_ANALYTICS_HEALTH` | `cmd.analytics.health` | req/reply |
| `CMD_DATA_DATASET_LIST_TABLES` | `cmd.data.dataset.list_tables` | req/reply |
| `CMD_DATA_DATASET_COVERAGE` | `cmd.data.dataset.coverage` | req/reply |
| `CMD_DATA_DATASET_ROWS` | `cmd.data.dataset.rows` | req/reply |
| `CMD_DATA_DATASET_EXPORT` | `cmd.data.dataset.export` | req/reply |
| `CMD_DATA_DATASET_INGEST` | `cmd.data.dataset.ingest` | req/reply |
| `CMD_DATA_DATASET_COMPUTE_FEATURES` | `cmd.data.dataset.compute_features` | req/reply |
| `CMD_DATA_DATASET_NORMALIZE_TF` | `cmd.data.dataset.normalize_timeframe` | req/reply |
| `CMD_DATA_DATASET_MAKE_TABLE` | `cmd.data.dataset.make_table_name` | req/reply |
| `CMD_DATA_DATASET_INSTRUMENT` | `cmd.data.dataset.instrument_details` | req/reply |
| `CMD_DATA_DATASET_SCHEMA` | `cmd.data.dataset.table_schema` | req/reply |
| `CMD_DATA_DATASET_MISSING` | `cmd.data.dataset.find_missing` | req/reply |
| `CMD_DATA_DATASET_TIMESTAMPS` | `cmd.data.dataset.timestamps` | req/reply |
| `CMD_DATA_DATASET_CONSTANTS` | `cmd.data.dataset.constants` | req/reply |
| `CMD_DATA_DB_PING` | `cmd.data.db.ping` | req/reply |
| `EVT_DATA_DATASET_UPDATED` | `events.data.dataset.updated` | event (out) |
| `CMD_ANALYTICS_TRAIN_START` | `cmd.analytics.train.start` | req/reply |
| `CMD_ANALYTICS_TRAIN_STATUS` | `cmd.analytics.train.status` | req/reply |
| `CMD_ANALYTICS_MODEL_LIST` | `cmd.analytics.model.list` | req/reply |
| `CMD_ANALYTICS_MODEL_LOAD` | `cmd.analytics.model.load` | req/reply |
| `CMD_ANALYTICS_PREDICT` | `cmd.analytics.predict` | req/reply |
| `EVT_ANALYTICS_TRAIN_PROGRESS` | `events.analytics.train.progress` | event (out) |
| `EVT_ANALYTICS_MODEL_READY` | `events.analytics.model.ready` | event (out) |

`reply_inbox(service, instance_id)` → `reply.{service}.{instance_id}`

#### schemas.py

| Класс | Поля | Описание |
|-------|------|----------|
| `Envelope` | `message_id`, `correlation_id`, `reply_to`, `issued_at`, `type`, `payload` | Универсальный конверт для каждого Kafka-сообщения |
| `HealthReply` | `status`, `service`, `version` | Типизированный payload ответа на `cmd.*.health` |

#### client.py

| Класс/функция | Описание |
|---------------|----------|
| `KafkaClient` | Async Kafka client (aiokafka). Singleton per service instance. Поля: `bootstrap_servers`, `service_name`, `instance_id`, `request_timeout` |
| `.start(subscribe=[...])` | Запуск producer + consumer. Авто-подписка на private reply inbox |
| `.register_handler(topic, async_fn)` | Регистрация async-обработчика. Если `reply_to` в конверте — ответ публикуется автоматически |
| `.request(topic, payload, timeout=…)` | Отправить команду, ждать ответ. Возвращает `dict` payload ответа |
| `.publish(topic, payload, ...)` | Fire-and-forget публикация |
