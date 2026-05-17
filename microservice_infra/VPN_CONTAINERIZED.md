# Containerized VPN Transport (рекомендуемый способ split-деплоя)

Этот документ описывает основной способ запуска ModelLine в split-режиме  
(backend-хост + admin-хост), реализованный через WireGuard-контейнер без  
ручной настройки системного wireguard/wg-quick/systemd на хостах.

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
        ↕  WireGuard UDP/51820  ↕
modelline-redpanda   :9092        admin-online → 10.44.0.1:9092 ✓
modelline-minio      :9000        admin-online → 10.44.0.1:9000 ✓
...                               (bridge → host → wg0 → WG → backend)
```

- VPN-контейнер работает с `network_mode: host` → интерфейс `wg0` появляется  
  на самом хосте, все Docker-контейнеры (в bridge-сети) достигают `10.44.0.1`  
  через таблицу маршрутизации хоста.
- Перед запуском entrypoint compose доустанавливает `wireguard-tools`,
  `iproute2-minimal` и `kmod`, чтобы внутри контейнера были доступны `wg`,
  `ip` и `modprobe`.
- Entry-point применяет live-конфиг через `wg-quick strip`, поэтому join token
  и `wg0-server.conf` могут оставаться в полном wg-quick формате с `Address`
  и `MTU`, не ломая `wg setconf`.
- Ключи генерируются один раз при первом запуске и сохраняются в  
  `.runtime-data/microservice_infra/vpn/` (на backend-хосте) и  
  `.runtime-data/microservice_admin/vpn/` (на admin-хосте).
- **Join token** — это `base64(client.conf)`: полная конфигурация WireGuard-клиента,  
  закодированная в одну строку. Передай её один раз при первом подключении admin-хоста.

---

## Предварительные требования

| Требование | Backend-хост | Admin-хост |
| --- | --- | --- |
| Docker Engine 24+ | ✅ | ✅ |
| Linux-ядро ≥ 5.6 (модуль `wireguard`) | ✅ | ✅ |
| Открытый UDP-порт **51820** | ✅ (входящий) | — |
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
VPN_SERVER_PORT=51820   # опционально, по умолчанию 51820
```

### 2. Запусти stack в режиме noadmin

```bash
./start.sh all noadmin
```

Launcher автоматически:

- Поднимет `modelline-vpn-server` с профилем `vpn` вместе с microservice_infra.
- Установит `REDPANDA_EXTERNAL_HOST=10.44.0.1` в `.env`.
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

# Интерфейс на хосте
ip addr show wg0
# Ожидаем: inet 10.44.0.1/24
```

### На admin-хосте

```bash
# Статус WireGuard клиента
docker exec modelline-vpn-client wg show

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
| В логах `Line unrecognized: \`Address=...\`` | На хосте ещё старая версия entrypoint/compose. Обнови код и заново выполни `./restart.sh all noadmin` или `./restart.sh all onlyadmin`; актуальная версия прогоняет конфиг через `wg-quick strip` перед `wg setconf` |
| `modelline-vpn-server` / `modelline-vpn-client` уходит в restart-loop | `docker logs modelline-vpn-server --tail 50` или `docker logs modelline-vpn-client --tail 50`; после фикса bootstrap-пакетов типовые оставшиеся причины уже host-level: нет `/dev/net/tun`, нет модуля `wireguard`, нет прав `NET_ADMIN` / `SYS_MODULE` |
| Join token не появляется | `docker logs modelline-vpn-server` |
| `wg0` не появился на хосте | `modinfo wireguard`; убедись что `/dev/net/tun` есть |
| Ping 10.44.0.1 не проходит | UDP 51820 открыт на backend? `wg show` на обоих хостах |
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
