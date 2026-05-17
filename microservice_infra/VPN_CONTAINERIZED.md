# Containerized VPN Transport (WireGuard over WebSocket/TCP 443)

Этот документ описывает основной способ запуска ModelLine в split-режиме  
(backend-хост + admin-хост), реализованный через WireGuard-контейнер и
WebSocket/TCP transport на `443` без ручной настройки системного
wireguard/wg-quick/systemd на хостах.

> Альтернативный (ручной) вариант с wg-quick + WStunnel описан в [WG_WSTUNNEL.md](WG_WSTUNNEL.md).

---

## Как это работает

```text
backend-хост                          admin-хост
─────────────────────────────         ─────────────────────────────
modelline-vpn-server                  modelline-vpn-client
  alpine:3.19 + runtime apk bootstrap   alpine:3.19 + runtime apk bootstrap
  network_mode: host                    network_mode: host
  wg0 → 10.44.0.1/24                   wg0 → 10.44.0.2/32
modelline-wstunnel-server :443       modelline-wstunnel-client
  ↕  WebSocket/TCP 443  ↕
  ↕  local UDP 51820    ↕
modelline-redpanda   :9092        admin-online → 10.44.0.1:9092 ✓
modelline-minio      :9000        admin-online → 10.44.0.1:9000 ✓
...                               (bridge → host → wg0 → WG → backend)
```

- VPN-контейнер работает с `network_mode: host` → интерфейс `wg0` появляется  
  на самом хосте, все Docker-контейнеры (в bridge-сети) достигают `10.44.0.1`  
  через таблицу маршрутизации хоста.
- WireGuard client больше не стучится напрямую в публичный UDP `51820`.
  В join token его `Endpoint` указывает на `127.0.0.1:51820`, а
  `wstunnel-client` переносит этот локальный UDP-поток в `ws://<backend>:443`.
  На backend-хосте `wstunnel-server` принимает TCP `443` и прокидывает поток
  в локальный WireGuard UDP `127.0.0.1:51820`.
- Перед запуском entrypoint compose доустанавливает `wireguard-tools`,
  `iproute2-minimal`, `kmod` и `iptables`, чтобы внутри контейнера были
  доступны `wg`, `ip`, `modprobe` и firewall bootstrap для `wg0`.
- Entry-point применяет live-конфиг через `wg-quick strip`, поэтому join token
  и `wg0-server.conf` могут оставаться в полном wg-quick формате с `Address`
  и `MTU`, не ломая `wg setconf`.
- На backend-host server entrypoint дополнительно вставляет idempotent
  `iptables` allow-правила для `wg0` на private TCP-порты `9092`, `9644`,
  `7510`, `7520`, `9000` и для ICMP. Это убирает зависимость containerized
  VPN от ручного `ufw allow in on wg0`, когда host firewall режет трафик уже
  после успешного WireGuard handshake.
- На admin-host client entrypoint дополнительно ставит route-ы из
  `AllowedIPs` на `wg0`, потому что `wg setconf` сам их не добавляет.
  Иначе можно получить успешный handshake, но нулевую связность до
  `10.44.0.1:*`.
- На admin-host client entrypoint также вставляет idempotent `iptables`
  allow-правила для `wg0` в `INPUT` и `DOCKER-USER`, чтобы ответный трафик
  backend -> admin не зависел от ручного `ufw allow in on wg0` или других
  host firewall-правил на admin-хосте.
- Ключи генерируются один раз при первом запуске и сохраняются в  
  `.runtime-data/microservice_infra/vpn/` (на backend-хосте) и  
  `.runtime-data/microservice_admin/vpn/` (на admin-хосте).
- **Join token** — это `base64(client.conf)`: полная конфигурация WireGuard-клиента,  
  закодированная в одну строку. В начале файла есть metadata-комментарии
  `VPN_SERVER_URL`, `VPN_WS_PORT`, `VPN_WS_PATH`: launcher читает их на
  admin-хосте и автоматически настраивает `wstunnel-client`.

---

## Предварительные требования

| Требование | Backend-хост | Admin-хост |
| --- | --- | --- |
| Docker Engine 24+ | ✅ | ✅ |
| Linux-ядро ≥ 5.6 (модуль `wireguard`) | ✅ | ✅ |
| Открытый TCP-порт **443** на backend-хосте | ✅ (входящий) | — |
| `/dev/net/tun` устройство | ✅ | ✅ |

**Проверить ядро**: `uname -r` (должно быть ≥ 5.6, Ubuntu 20.04+ / Debian 11+ подходят).  
**Проверить модуль**: `modinfo wireguard` — должен вернуть информацию о модуле.  
На Ubuntu 18.04 / Debian 10 установи `wireguard-dkms`.

---

## Настройка backend-хоста

### 1. Задай публичный IP/hostname в `.env`

```bash
# microservice_infra/.env
VPN_SERVER_URL=<публичный IP или hostname backend-хоста>
VPN_TRANSPORT=ws
VPN_WS_PORT=443
VPN_WS_PATH=modelline-wg
VPN_SERVER_PORT=51820   # локальный UDP WireGuard за wstunnel, наружу не открываем
```

### 2. Запусти stack в режиме noadmin

```bash
./start.sh all noadmin
```

Launcher автоматически:

- Поднимет `modelline-vpn-server` с профилем `vpn` вместе с microservice_infra.
- Поднимет `modelline-wstunnel-server` на TCP `443` вместе с WireGuard.
- Установит `REDPANDA_EXTERNAL_HOST=10.44.0.1` в `.env`.
- Разрешит private backend-порты по `wg0` через host `iptables` прямо из
  `modelline-vpn-server`.
- Дождётся генерации ключей и распечатает **join token** в консоль.

Пример вывода:

```text
╔══════════════════════════════════════════════════════════════════╗
║         VPN JOIN TOKEN — скопируй на admin-хост                 ║
╠══════════════════════════════════════════════════════════════════╣
║  Backend WG IP : 10.44.0.1                                      ║
║                                                                  ║
║  На admin-хосте запусти:                                         ║
║  ./start.sh all onlyadmin <JOIN_TOKEN>                           ║
╚══════════════════════════════════════════════════════════════════╝

<long-base64-string>
```

Скопируй join token (длинную строку base64).

---

## Настройка admin-хоста

### Первый запуск (с join token)

```bash
./start.sh all onlyadmin <JOIN_TOKEN>
```

Launcher автоматически:

- Декодирует join token и записывает `wg0.conf` в  
  `.runtime-data/microservice_admin/vpn/wg0.conf`.
- Прочитает metadata из join token и заполнит `VPN_SERVER_URL`, `VPN_WS_PORT`,
  `VPN_WS_PATH`, `VPN_CLIENT_LOCAL_PORT` в `microservice_admin/.env`.
- Поднимет `modelline-wstunnel-client`, который слушает локальный UDP `51820`
  и соединяется с backend WebSocket endpoint на TCP `443`.
- Поднимет `modelline-vpn-client` (WireGuard клиент).
- Дождётся, пока `wg0` поднимется (`10.44.0.2/32` на хосте).
- Установит `ONLINE_*` переменные на `10.44.0.1:*`.
- Поднимет `admin-online`.

### Последующие перезапуски (join token не нужен)

После первого запуска `wg0.conf` сохранён локально — join token больше не нужен:

```bash
./start.sh all onlyadmin
# или
./restart.sh all onlyadmin
```

Если хочешь задать новый join token (ротация ключей):

```bash
./start.sh all onlyadmin <NEW_JOIN_TOKEN>
```

---

## Верификация

### На backend-хосте

```bash
# Статус WireGuard сервера
docker exec modelline-vpn-server wg show

# Статус WebSocket transport
docker logs modelline-wstunnel-server --tail 50

# Интерфейс на хосте
ip addr show wg0
# Ожидаем: inet 10.44.0.1/24
```

### На admin-хосте

```bash
# Статус WireGuard клиента
docker exec modelline-vpn-client wg show

# Статус WebSocket transport
docker logs modelline-wstunnel-client --tail 50

# Интерфейс на хосте
ip addr show wg0
# Ожидаем: inet 10.44.0.2/32

# Пинг backend через VPN
ping -c 3 10.44.0.1

# Проверить Redpanda через VPN
nc -zv 10.44.0.1 9092
```

---

## Безопасность

По умолчанию сервисы backend-хоста слушают на `0.0.0.0:*`.  
Для ограничения доступа через VPN можно добавить `BIND_ADDR=10.44.0.1`  
в `microservice_infra/.env`:

```bash
REDPANDA_BIND_ADDR=10.44.0.1
MINIO_BIND_ADDR=10.44.0.1
```

Тогда сервисы будут принимать соединения только через WireGuard-интерфейс.

---

## Устранение проблем

| Проблема | Что проверить |
| --- | --- |
| `wstunnel-client` не подключается | На backend-host должен быть открыт входящий `443/tcp`; проверь `docker logs modelline-wstunnel-server --tail 50` и `docker logs modelline-wstunnel-client --tail 50` |
| `latest handshake` есть, но `ping 10.44.0.1` и `10.44.0.1:<port>` не работают | На admin-host проверь `ip route get 10.44.0.1`; корректный путь должен идти через `dev wg0`. После обновления кода нужен новый `./restart.sh all onlyadmin`, потому что актуальный `vpn-client` теперь сам ставит route-ы из `AllowedIPs` и добавляет `iptables` allow для `wg0`. Если backend-host ещё не обновлялся под server-side firewall bootstrap, отдельно выполни `./restart.sh all noadmin` |
| В логах `Line unrecognized: \`Address=...\`` | На хосте ещё старая версия entrypoint/compose. Обнови код и заново выполни `./restart.sh all noadmin` или `./restart.sh all onlyadmin`; актуальная версия прогоняет конфиг через `wg-quick strip` перед `wg setconf` |
| `modelline-vpn-server` / `modelline-vpn-client` уходит в restart-loop | `docker logs modelline-vpn-server --tail 50` или `docker logs modelline-vpn-client --tail 50`; после фикса bootstrap-пакетов типовые оставшиеся причины уже host-level: нет `/dev/net/tun`, нет модуля `wireguard`, нет прав `NET_ADMIN` / `SYS_MODULE` |
| Join token не появляется | `docker logs modelline-vpn-server` |
| `wg0` не появился на хосте | `modinfo wireguard`; убедись что `/dev/net/tun` есть |
| Ping 10.44.0.1 не проходит | TCP 443 открыт на backend? `docker logs modelline-wstunnel-client`, `docker exec modelline-vpn-client wg show` на admin-host и `docker exec modelline-vpn-server wg show` на backend-host |
| `base64 -w 0` ошибка | На macOS используй `base64` без флага (скрипт обрабатывает оба варианта) |
| `.ready` не создаётся | `docker logs modelline-vpn-server` или `modelline-vpn-client` |

---

## Файловая структура состояния

```text
.runtime-data/
  microservice_infra/vpn/
    server.key          # приватный ключ сервера (не передавай!)
    server.pub          # публичный ключ сервера
    client.key          # приватный ключ клиента
    client.pub          # публичный ключ клиента
    psk.key             # pre-shared key
    wg0-server.conf     # конфигурация WireGuard-сервера
    client.conf         # конфигурация для клиента (= join token до base64)
    .ready              # маркер: ключи сгенерированы и wg0 поднят
  microservice_admin/vpn/
    wg0.conf            # конфигурация клиента (декодированный join token)
    .ready              # маркер: wg0 поднят
```
