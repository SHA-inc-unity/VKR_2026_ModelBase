# microservice_infra — Структура

> Обновляй этот файл при добавлении/изменении компонентов инфраструктуры.

## Связанная документация

- [README.md](README.md) — порты, запуск и архитектурные правила infra-слоя
- [../docs/agents/services/microservice_infra.md](../docs/agents/services/microservice_infra.md) — профиль сервиса для agent workflow
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first маршрут работы

---

## Корень

| Файл | Описание |
| ---- | -------- |
| `docker-compose.yml` | Запускает: Redpanda, Redpanda Console, MinIO, MinIO Console + init и **nginx** (всегда, без profile-флагов) — ingress/download endpoint backend/full-стека на host-порте 8501 (override через `NGINX_PORT`). `nginx` проксирует `/admin/*` → `admin:3000` и `/modelline-blobs/*` → `minio:9000`. Данные Redpanda и MinIO хранятся в repo-local bind mounts `../.runtime-data/microservice_infra/{redpanda,minio}`. В split deployment Redpanda external advertise address и host-порты configurable через `REDPANDA_EXTERNAL_HOST`, `REDPANDA_EXTERNAL_PORT`, `REDPANDA_ADMIN_PORT`, `REDPANDA_CONSOLE_PORT`, `MINIO_API_PORT`, `MINIO_CONSOLE_PORT`. |
| `nginx/nginx.conf` | Конфиг nginx: `default_server`, `/admin/*` → admin:3000 (включая `/admin/api/events` SSE), `/modelline-blobs/*` → minio:9000 без буферизации (`proxy_buffering off`, `proxy_request_buffering off`, `proxy_read_timeout 3600s`, `client_max_body_size 0` — для многогигабайтных CSV/ZIP экспортов). В `noadmin` deployment этот nginx остаётся download ingress-ом backend-хоста, а отдельная remote admin-head не обязана ходить через локальный `/admin/*`. |
| `docker-compose.yml` | Запускает: Redpanda, Redpanda Console, MinIO, MinIO Console + init и **nginx** (всегда, без profile-флагов) — ingress/download endpoint backend/full-стека на host-порте 8501 (override через `NGINX_PORT`). `nginx` проксирует `/admin/*` → `admin:3000` и `/modelline-blobs/*` → `minio:9000`. Данные Redpanda и MinIO хранятся в repo-local bind mounts `../.runtime-data/microservice_infra/{redpanda,minio}`. В split deployment Redpanda external advertise address и host-порты configurable через `REDPANDA_EXTERNAL_HOST`, `REDPANDA_EXTERNAL_PORT`, `REDPANDA_ADMIN_PORT`, `REDPANDA_CONSOLE_PORT`, `MINIO_API_PORT`, `MINIO_CONSOLE_PORT`. VPN compose-сервис bootstrap-ит `wireguard-tools`, `iproute2-minimal`, `kmod` и `iptables` перед запуском entrypoint; `wstunnel-server` публикует WebSocket/TCP transport на `VPN_WS_PORT` (`443` по умолчанию). |
| `vpn/server-entrypoint.sh` | Entrypoint для compose-сервиса `vpn` (profile `vpn`): генерирует WireGuard ключи, пишет `wg0-server.conf` + `client.conf`, затем поднимает интерфейс `wg0` (`10.44.0.1/24`) и пишет `.ready` маркер. Перед `wg setconf` прогоняет `wg0-server.conf` через `wg-quick strip`, чтобы wg-quick-поля (`Address`) не ломали wireguard-native apply path. После подъёма `wg0` вставляет idempotent `iptables` allow-правила для private backend TCP-портов и ICMP по интерфейсу `wg0`. Ожидает, что compose уже подтянул `wireguard-tools`, `iproute2-minimal`, `kmod`, `iptables`. |
| `.env.example` | Базовые infra/env defaults: `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `REDPANDA_EXTERNAL_HOST`, `REDPANDA_EXTERNAL_PORT`, `REDPANDA_ADMIN_PORT`, `REDPANDA_CONSOLE_PORT`, `MINIO_API_PORT`, `MINIO_CONSOLE_PORT`, `NGINX_PORT` |
| `VPN_CONTAINERIZED.md` | **Рекомендуемый** split-deployment: containerized WireGuard без ручных хостовых настроек, join token UX, wg0 через docker container |
| `WG_WSTUNNEL.md` | Fallback: ручная split-deployment схема WireGuard over WStunnel, адресный план, firewall rules, `ONLINE_*` и `REDPANDA_EXTERNAL_HOST` |
| `README.md` | Описание, порты, правила использования |

---

## Сервисы docker-compose

| Сервис | Образ | Порты (host) | Описание |
| ------ | ----- | ------------ | -------- |
| `redpanda` | `redpandadata/redpanda:v24.1.9` | `9092` (EXTERNAL), `9644` (admin) | Kafka-API брокер (KRaft, single-node). Внутри сети: `redpanda:29092` |
| `redpanda-console` | `redpandadata/console` | `8080` | Web UI для топиков и consumer-групп |
| `redpanda-init` | `redpandadata/redpanda` | — | One-shot: `topic_partitions_per_shard=10000`, `auto_create_topics_enabled=true` |
| `redpanda-janitor` | `redpandadata/redpanda` | — | 6-часовой sweep осиротевших `reply.*` топиков (только пустые, с HW=0); не трогает активные long-lived reply-inbox'ы |
| `minio` | `minio/minio` | `9000` (API), `9001` (Console) | S3-совместимое хранилище для claim-check паттерна |
| `minio-init` | `minio/mc` | — | One-shot контейнер: создаёт bucket `modelline-blobs` после старта MinIO |
| `nginx` | `nginx:1.27-alpine` | host `8501` → container `80` | Browser-facing ingress local/full стека и download ingress backend-host'а в split deployment: `/admin/*` → admin:3000 (если локальный admin поднят), `/modelline-blobs/*` → minio:9000 без ломания signed path/query. Поднимается всегда, без profile-флага. |
| `vpn` | `alpine:3.19` | host network, local UDP `51820` | WireGuard server: поднимает `wg0=10.44.0.1/24`, генерирует join token, добавляет firewall allow для `wg0`. |
| `wstunnel-server` | `ghcr.io/erebe/wstunnel` | host `443` TCP | WebSocket/TCP transport для WireGuard: принимает `ws://0.0.0.0:443` и прокидывает в локальный UDP `127.0.0.1:51820`. |

---

## Сеть

| Сеть | Описание |
| ---- | -------- |
| `modelline_net` | Docker bridge-сеть. Создаётся этим compose. Все остальные сервисы подключаются как `external: true` |

---

## Endpoints (внутри сети `modelline_net`)

| Сервис | Адрес |
| ------ | ----- |
| Kafka broker | `redpanda:29092` |
| MinIO S3 API (для signing внутри сервисов) | `http://minio:9000` |
| MinIO Console | `http://localhost:9001` |
| Redpanda Console | `http://localhost:8080` |
| Browser-facing local/full-stack entry | `http://localhost:8501/admin/` |
| Browser-facing claim-check download path | `http://localhost:8501/modelline-blobs/...` |

---

## Архитектурное правило

Все межсервисные коммуникации в платформе ModelLine — **только через Kafka** (Redpanda). HTTP между сервисами запрещён. MinIO используется исключительно для claim-check: передача больших данных (CSV-экспорт, ingestion-блобы) через S3 URL вместо Kafka payload.
