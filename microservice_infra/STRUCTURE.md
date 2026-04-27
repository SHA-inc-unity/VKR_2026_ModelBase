# microservice_infra — Структура

> Обновляй этот файл при добавлении/изменении компонентов инфраструктуры.

---

## Корень

| Файл | Описание |
|------|----------|
| `docker-compose.yml` | Запускает: Redpanda, Redpanda Console, MinIO, MinIO Console + init |
| `.env.example` | `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `REDPANDA_CONSOLE_PORT` |
| `README.md` | Описание, порты, правила использования |

---

## Сервисы docker-compose

| Сервис | Образ | Порты (host) | Описание |
|--------|-------|-------------|----------|
| `redpanda` | `redpandadata/redpanda:v24.1.9` | `9092` (EXTERNAL), `9644` (admin) | Kafka-API брокер (KRaft, single-node). Внутри сети: `redpanda:29092` |
| `redpanda-console` | `redpandadata/console` | `8080` | Web UI для топиков и consumer-групп |
| `redpanda-init` | `redpandadata/redpanda` | — | One-shot: `topic_partitions_per_shard=10000`, `auto_create_topics_enabled=true` |
| `redpanda-janitor` | `redpandadata/redpanda` | — | 6-часовой sweep осиротевших `reply.*` топиков (только пустые, с HW=0); не трогает активные long-lived reply-inbox'ы |
| `minio` | `minio/minio` | `9000` (API), `9001` (Console) | S3-совместимое хранилище для claim-check паттерна |
| `minio-init` | `minio/mc` | — | One-shot контейнер: создаёт bucket `modelline-blobs` после старта MinIO |

---

## Сеть

| Сеть | Описание |
|------|----------|
| `modelline_net` | Docker bridge-сеть. Создаётся этим compose. Все остальные сервисы подключаются как `external: true` |

---

## Endpoints (внутри сети `modelline_net`)

| Сервис | Адрес |
|--------|-------|
| Kafka broker | `redpanda:29092` |
| MinIO S3 API | `http://minio:9000` |
| MinIO Console | `http://localhost:9001` |
| Redpanda Console | `http://localhost:8080` |

---

## Архитектурное правило

Все межсервисные коммуникации в платформе ModelLine — **только через Kafka** (Redpanda). HTTP между сервисами запрещён. MinIO используется исключительно для claim-check: передача больших данных (CSV-экспорт, ingestion-блобы) через S3 URL вместо Kafka payload.
