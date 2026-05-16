# WG + WStunnel For Split Deployment

Рекомендуемая схема для split deployment ModelLine:

- backend-хост поднимает `microservice_infra`, `microservice_data`, `microservice_analitic`, `microservice_account`, `microservice_gateway` в режиме `noadmin`
- отдельный admin-хост поднимает только `microservice_admin` в режиме `onlyadmin`
- общая приватная сеть между хостами строится через **WireGuard**, а сам UDP WireGuard обфусцируется и пробрасывается через **WStunnel (WSS/443)**

Эта схема подходит ModelLine, потому что cross-host трафик здесь в основном control-plane:

- Kafka request/reply и SSE
- health probes до backend endpoint-ов
- редкие admin API вызовы

Большие dataset-download файлы по-прежнему должны идти напрямую с backend ingress-а через `/modelline-blobs/*`, а не через WG-туннель.

## Почему именно так

- Внешне открыт только `443/tcp` для WStunnel на backend-хосте и UI ingress admin-хоста.
- Backend-порты `9092`, `9644`, `7510`, `7520`, `9000` остаются приватными и доступны только через WG.
- `microservice_admin` уже умеет работать как remote `admin-online` через `ONLINE_*`.
- После выноса `REDPANDA_EXTERNAL_HOST` из `localhost` Kafka корректно advertise'ит backend WG-адрес.

## Топология

```text
Browser
  |
  v
https://admin.example.com/admin/*
  |
  v
admin-host: microservice_admin (admin-online)
  |
  |  WireGuard over WStunnel (WSS/443)
  v
backend-host WG IP 10.44.0.1
  |
  +--> Redpanda      10.44.0.1:9092
  +--> Redpanda API  10.44.0.1:9644
  +--> Account       10.44.0.1:7510
  +--> Gateway       10.44.0.1:7520
  +--> MinIO health  10.44.0.1:9000

Browser downloads
  |
  v
https://downloads.example.com/modelline-blobs/*
  |
  v
backend-host nginx -> minio:9000
```

## Адресный план

Пример приватной WG-сети:

- backend-хост: `10.44.0.1/24`
- admin-хост: `10.44.0.2/24`

Рекомендуемый маршрут для admin-хоста:

- `AllowedIPs = 10.44.0.0/24`

Не делай full-tunnel (`0.0.0.0/0`) для этой задачи: ModelLine нужен только приватный транспорт между двумя узлами, а не дефолтный маршрут всего трафика через backend.

## Backend host

### 1. WireGuard server

Пример `/etc/wireguard/wg0.conf`:

```ini
[Interface]
Address = 10.44.0.1/24
ListenPort = 51820
PrivateKey = <backend_private_key>

[Peer]
PublicKey = <admin_public_key>
AllowedIPs = 10.44.0.2/32
```

### 2. WStunnel server

По официальному примеру WStunnel для WireGuard сервер должен принимать WSS на `443/tcp` и пробрасывать только локальный UDP-порт WG:

```bash
wstunnel server \
  --restrict-to localhost:51820 \
  --restrict-http-upgrade-path-prefix <shared_secret_path> \
  --tls-certificate /etc/letsencrypt/live/backend.example.com/fullchain.pem \
  --tls-private-key /etc/letsencrypt/live/backend.example.com/privkey.pem \
  wss://[::]:443
```

Практическое правило:

- используй **реальный TLS-сертификат**, не встроенный self-signed
- используй `--restrict-http-upgrade-path-prefix`, чтобы WStunnel не принимал произвольные upgrade-запросы

### 3. ModelLine backend env

В `microservice_infra/.env` обязательно укажи backend WG-адрес или приватный DNS, который резолвится в этот WG-адрес:

```env
REDPANDA_EXTERNAL_HOST=10.44.0.1
REDPANDA_EXTERNAL_PORT=9092
REDPANDA_ADMIN_PORT=9644
NGINX_PORT=8501
```

Это критично: если оставить `REDPANDA_EXTERNAL_HOST=localhost`, remote `admin-online` подключится к bootstrap broker, получит metadata и затем попытается говорить с `localhost:9092` у самого себя.

Для dataset downloads на backend-хосте оставь отдельный browser-facing ingress, например:

```env
PUBLIC_DOWNLOAD_BASE_URL=https://downloads.example.com
```

Эта переменная живёт в `microservice_data`, не в `microservice_infra`.

### 4. Launcher mode on backend

На backend-хосте:

```bash
cd /srv/ModelLine/microservicestarter
./start.sh all noadmin
```

или:

```bash
./restart.sh all noadmin
```

## Admin host

### 1. WStunnel client

По официальному примеру WireGuard-over-WStunnel client должен поднимать локальный UDP-listener и указывать его потом в `Endpoint` WireGuard-клиента:

```bash
wstunnel client \
  --http-upgrade-path-prefix <shared_secret_path> \
  -L 'udp://51820:localhost:51820?timeout_sec=0' \
  wss://backend.example.com:443
```

### 2. WireGuard client

Пример `/etc/wireguard/wg0.conf` на admin-хосте:

```ini
[Interface]
Address = 10.44.0.2/32
PrivateKey = <admin_private_key>
DNS = 1.1.1.1
MTU = 1380

[Peer]
PublicKey = <backend_public_key>
AllowedIPs = 10.44.0.0/24
Endpoint = 127.0.0.1:51820
PersistentKeepalive = 20
```

Почему `Endpoint = 127.0.0.1:51820`:

- WireGuard клиент стучится в локальный UDP-порт WStunnel клиента
- WStunnel уже заворачивает этот UDP трафик в `wss://backend.example.com:443`

### 3. ModelLine admin env

В `microservice_admin/.env` для режима `admin-online` используй WG-адреса backend-хоста:

```env
ONLINE_KAFKA_BOOTSTRAP_SERVERS=10.44.0.1:9092
ONLINE_REDPANDA_ADMIN_URL=10.44.0.1:9644
ONLINE_ACCOUNT_URL=10.44.0.1:7510
ONLINE_GATEWAY_URL=10.44.0.1:7520
ONLINE_MINIO_URL=10.44.0.1:9000
ONLINE_REDIS_URL=
ADMIN_PORT=8501
```

### 4. Launcher mode on admin

На admin-хосте:

```bash
cd /srv/ModelLine/microservicestarter
./start.sh all onlyadmin
```

или:

```bash
./restart.sh all onlyadmin
```

## Firewall / exposure rules

### Public and private ports on backend host

Публично:

- `443/tcp` — WStunnel server
- `8501/tcp` или `443/tcp` через отдельный reverse proxy — download ingress для `/modelline-blobs/*`, если он нужен браузеру снаружи

Только по WG/private allowlist:

- `9092/tcp` — Kafka broker
- `9644/tcp` — Redpanda admin API
- `7510/tcp` — Account API
- `7520/tcp` — Gateway API
- `9000/tcp` — MinIO health endpoint, если хочешь видеть его в admin health panel

### Public ports on admin host

Публично:

- `8501/tcp` или `443/tcp` через reverse proxy — UI ingress `admin-online`

## Проверка после поднятия

С admin-хоста после `wg-quick up wg0`:

```bash
ping 10.44.0.1
curl http://10.44.0.1:9644/v1/status/ready
curl http://10.44.0.1:7510/health
curl http://10.44.0.1:7520/health
```

Если Kafka reachable, но `microservice_admin` всё ещё не подключается, первым делом проверь, что backend advertise'ит не `localhost`, а WG-адрес:

```bash
docker compose exec redpanda rpk cluster info -X brokers=127.0.0.1:9092
```

В metadata должен фигурировать `10.44.0.1:9092` или соответствующий private DNS.

## Ограничения и trade-offs

- WG over WStunnel означает UDP-over-WebSocket/TLS поверх TCP. Для latency-sensitive data-plane это не лучший вариант.
- Для ModelLine этот компромисс приемлем, потому что через split-линк идёт в основном admin/control-plane трафик.
- Не пускай большие browser downloads через admin-host: оставляй их на backend ingress-е.
- Если всё же включишь full-tunnel `AllowedIPs = 0.0.0.0/0`, добавь отдельный static route до публичного IP backend WStunnel server-а через основной gateway, иначе можно закольцевать WStunnel внутри самого WG. Для рекомендуемого `10.44.0.0/24` это не требуется.
