# microservice_infra

Общая инфраструктура ModelLine:

| Компонент         | Порт (host) | Назначение                                     |
|-------------------|-------------|------------------------------------------------|
| **Nginx**         | **8501**    | **Локальный ingress и download endpoint backend/full-стека** |
| Redpanda          | 9092        | Kafka-API брокер (единственный канал IPC)      |
| Redpanda Console  | 8080        | UI для топиков / consumer groups               |
| MinIO             | 9000        | S3-совместимое хранилище (внутренний; наружу как download path не публикуется) |
| MinIO Console     | 9001        | UI MinIO (логин из `.env`)                     |

Создаёт docker-сеть **`modelline_net`**, к которой подключаются остальные
сервисы (`microservice_data`, `microservice_admin`, `microservice_analitic`).

## Документация для агентов

- [STRUCTURE.md](STRUCTURE.md) — карта инфраструктурных компонентов и compose-слоя
- [../docs/agents/services/microservice_infra.md](../docs/agents/services/microservice_infra.md) — профиль сервиса для agent workflow
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first маршрут работы

## Nginx — локальный внешний вход backend/full-стека

Файл конфигурации: `nginx/nginx.conf`. Контейнер слушает порт 80, в
docker-compose публикуется как host-порт **`8501`** (override через
`NGINX_PORT`). В local/full stack это browser-facing вход платформы:

| URL                          | Куда проксируется          |
|------------------------------|----------------------------|
| `localhost:8501/`            | 301 → `/admin/`            |
| `localhost:8501/admin`       | 301 → `/admin/`            |
| `localhost:8501/admin/*`     | `http://admin:3000` (Next.js basePath=/admin) |
| `localhost:8501/admin/api/events` | `http://admin:3000` (SSE, без буферизации) |
| `localhost:8501/modelline-blobs/*` | `http://minio:9000` (signed dataset downloads, path/query сохраняются как есть) |

В local/full stack `microservice_admin` сам наружу не публикуется — он
живёт только в `modelline_net` под именем `admin:3000`. MinIO снаружи
как browser download path тоже не публикуется: presigned URL-ы выдаются
с host'ом `localhost:8501` и проксируются nginx'ом в `minio:9000` без
потери SigV4-подписи (MinIO не биндит подпись к заголовку Host).

В split deployment backend-хост может работать в режиме `noadmin`:
тогда этот nginx остаётся download ingress-ом для `/modelline-blobs/*`,
а сама admin-head живёт отдельно в режиме `onlyadmin` на другой машине.
Локальный `/admin/*` в таком сценарии не считается рабочей UI-точкой.

`server_name` в nginx-конфиге — `default_server`/`_`, поэтому вход
работает для любого hostname (локально `localhost:8501`, в
проде/homelab — публичный домен/Tunnel перед этим же портом).

**HTTPS:** добавить certbot + смонтировать ssl.conf в
`/etc/nginx/conf.d/`; открыть на хосте `:443`.

## Запуск

```powershell
cp .env.example .env
docker compose up -d
# → http://localhost:8080            (Redpanda Console)
# → http://localhost:9001            (MinIO Console)
# → http://localhost:8501/admin/     (Admin panel через единый вход)
```

Nginx поднимается всегда вместе с остальной infra — никаких опциональных
profile'ов или интерактивных prompt'ов: новая внешняя топология должна
стартовать штатным `docker compose up -d`.

## Endpoints (внутри сети)

- `KAFKA_BOOTSTRAP_SERVERS=redpanda:29092`
- `S3_ENDPOINT_URL=http://minio:9000`
- S3 bucket для блобов: **`modelline-blobs`** (создаётся `minio-init`)

## Архитектурное правило

Вся межсервисная коммуникация ML-платформы идёт через Kafka. HTTP между
application-сервисами **запрещён**. HTTP допустим только на внешнем
входе (этот nginx) и внутри infra-слоя. Большие блобы (CSV-экспорты,
.cbm модели, anomaly-отчёты, training-наборы) передаются по claim-check
паттерну: файл кладётся в MinIO, в Kafka летит только presigned
URL/claim-check; **браузер качает напрямую из MinIO через
`/modelline-blobs/*`** на том же origin, что и admin-панель — байты не
проходят через admin-приложение и не упираются в memory limits Next.js
runtime'а.

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
