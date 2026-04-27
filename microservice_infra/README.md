# microservice_infra

Общая инфраструктура ModelLine:

| Компонент         | Порт (host) | Назначение                                     |
|-------------------|-------------|------------------------------------------------|
| Redpanda          | 9092        | Kafka-API брокер (единственный канал IPC)      |
| Redpanda Console  | 8080        | UI для топиков / consumer groups               |
| MinIO             | 9000        | S3-совместимое хранилище для claim-check       |
| MinIO Console     | 9001        | UI MinIO (логин из `.env`)                     |
| **Nginx**         | **80**      | **Reverse proxy: sha-trade.tech → сервисы**   |

Создаёт docker-сеть **`modelline_net`**, к которой подключаются остальные
сервисы (`microservice_data`, `microservice_admin`, `microservice_analitic`).

## Nginx — маршрутизация домена

Файл конфигурации: `nginx/nginx.conf`

| URL                          | Куда проксируется          |
|------------------------------|----------------------------|
| `sha-trade.tech/`            | 301 → `/admin/`            |
| `sha-trade.tech/admin`       | 301 → `/admin/`            |
| `sha-trade.tech/admin/*`     | `http://admin:3000`        |
| `sha-trade.tech/admin/api/events` | `http://admin:3000` (SSE, без буферизации) |

**Требования для работы домена:**
1. DNS A-запись `sha-trade.tech` → публичный IP сервера
2. Порт 80 открыт в firewall сервера
3. Admin-панель собрана с `basePath: '/admin'` в `next.config.js` (см. промпт ниже)
4. Admin-контейнер запущен (`microservice_admin`)

**HTTPS:** добавить certbot + смонтировать ssl.conf; открыть порт 443.

## Запуск

```powershell
cp .env.example .env
docker compose up -d
# → http://localhost:8080  (Redpanda Console)
# → http://localhost:9001  (MinIO Console)
# → http://sha-trade.tech/admin  (Admin panel, при настроенном DNS)
```

## Endpoints (внутри сети)

- `KAFKA_BOOTSTRAP_SERVERS=redpanda:29092`
- `S3_ENDPOINT_URL=http://minio:9000`
- S3 bucket для блобов: **`modelline-blobs`** (создаётся `minio-init`)

## Архитектурное правило

Вся межсервисная коммуникация идёт через Kafka. HTTP между сервисами
**запрещён**. Большие блобы (CSV-экспорты, .cbm модели, anomaly-отчёты,
training-наборы) передаются по claim-check pattern: файл кладётся в MinIO,
в Kafka летит только presigned URL/claim-check.

## Reply-topic janitor

Контейнер `redpanda-janitor` подметает осиротевшие `reply.<svc>.<uuid>`
топики. После миграции Admin/SSE/Analitic на **long-lived reply-inbox**
(один топик на жизнь процесса) поток новых ephemeral-топиков иссяк, поэтому:

- Интервал поднят до **6 часов** (раньше 30 мин). Сейчас в стационарном
  режиме топиков всего по одному на каждый запущенный admin/gateway/analitic
  процесс — подметать чаще нечего.
- Удаляются только **пустые** `reply.*` топики (HighWatermark = 0). Топик с
  ненулевым HW означает, что в нём была хотя бы одна запись, то есть
  consumer когда-то реально работал; такой топик трогать не безопасно
  (может оказаться чьим-то активным reply-inbox).
- Активные long-lived inbox'и принимают reply-сообщения постоянно, поэтому
  их HW > 0, и они автоматически защищены от удаления.

Конфиг кластера (через `redpanda-init`):
- `topic_partitions_per_shard=10000` — единственный shard в dev-режиме
  должен помещать ~N реплай-топиков; после миграции это с большим запасом.
- `auto_create_topics_enabled=true` — для упрощённого dev UX.

