#!/bin/sh
# WireGuard server entrypoint for ModelLine split deployment.
# Runs inside modelline-vpn-server container (alpine:3.19 + wireguard-tools).
# network_mode: host — wg0 appears directly on the HOST network namespace.
#
# State directory: /vpn/state  (mounted from .runtime-data/microservice_infra/vpn)
#   server.key / server.pub — server WireGuard keypair (generated on first run)
#   client.key / client.pub — client WireGuard keypair (pre-generated for the join token)
#   psk                     — WireGuard preshared key
#   wg0-server.conf         — server wg-quick config
#   client.conf             — client wg0.conf (base64-encoded = the join token)
#   .ready                  — touched after config is ready; launcher waits for this

set -e

STATE=/vpn/state
mkdir -p "$STATE"

VPN_SERVER_IP="${VPN_SERVER_IP:-10.44.0.1}"
VPN_CLIENT_IP="${VPN_CLIENT_IP:-10.44.0.2}"
VPN_SUBNET="${VPN_SUBNET:-10.44.0.0/24}"
VPN_SERVER_PORT="${VPN_SERVER_PORT:-51820}"
VPN_ALLOWED_TCP_PORTS="${VPN_ALLOWED_TCP_PORTS:-9092,9644,7510,7520,9000}"
VPN_TRANSPORT="${VPN_TRANSPORT:-ws}"
VPN_WS_PORT="${VPN_WS_PORT:-8443}"
VPN_WS_PATH="${VPN_WS_PATH:-modelline-wg}"
VPN_CLIENT_LOCAL_PORT="${VPN_CLIENT_LOCAL_PORT:-51820}"

ensure_iptables_rule() {
    local chain="$1"
    shift
    if ! command -v iptables >/dev/null 2>&1; then
        echo "[vpn-server] WARNING: iptables не найден — пропускаем firewall bootstrap."
        return 0
    fi

    if iptables -C "$chain" "$@" 2>/dev/null; then
        return 0
    fi

    iptables -I "$chain" 1 "$@"
}

allow_wg_firewall_input() {
    ensure_iptables_rule INPUT -i wg0 -p tcp -m multiport --dports "$VPN_ALLOWED_TCP_PORTS" -j ACCEPT
    ensure_iptables_rule INPUT -i wg0 -p icmp -j ACCEPT

    if iptables -S DOCKER-USER >/dev/null 2>&1; then
        ensure_iptables_rule DOCKER-USER -i wg0 -p tcp -m multiport --dports "$VPN_ALLOWED_TCP_PORTS" -j ACCEPT
    fi
}

# Try to load the WireGuard kernel module; fall back to wireguard-go if needed.
modprobe wireguard 2>/dev/null || true

# ── Key generation (first run only) ─────────────────────────────────────────
if [ ! -f "$STATE/server.key" ]; then
    echo "[vpn-server] First run — generating WireGuard keypairs..."
    wg genkey > "$STATE/server.key"
    wg pubkey < "$STATE/server.key" > "$STATE/server.pub"
    wg genpsk > "$STATE/psk"
    wg genkey > "$STATE/client.key"
    wg pubkey < "$STATE/client.key" > "$STATE/client.pub"
    echo "[vpn-server] Keypairs generated."
fi

SERVER_PRIVKEY=$(cat "$STATE/server.key")
SERVER_PUBKEY=$(cat "$STATE/server.pub")
PSK=$(cat "$STATE/psk")
CLIENT_PRIVKEY=$(cat "$STATE/client.key")
CLIENT_PUBKEY=$(cat "$STATE/client.pub")

# Build the Endpoint value for the client config.
# In WebSocket mode WireGuard sends UDP to a local wstunnel-client listener;
# wstunnel carries that UDP over TCP/443 to the backend host.
if [ "$VPN_TRANSPORT" = "ws" ]; then
    ENDPOINT="127.0.0.1:${VPN_CLIENT_LOCAL_PORT}"
elif [ -n "${VPN_SERVER_URL:-}" ]; then
    ENDPOINT="${VPN_SERVER_URL}:${VPN_SERVER_PORT}"
else
    # Placeholder — operator must patch client.conf manually when VPN_SERVER_URL is not set.
    ENDPOINT="<BACKEND_PUBLIC_IP>:${VPN_SERVER_PORT}"
    echo "[vpn-server] WARNING: VPN_SERVER_URL is not set. Client Endpoint will be a placeholder."
    echo "[vpn-server] Set VPN_SERVER_URL in microservice_infra/.env and restart to fix."
fi

if [ "$VPN_TRANSPORT" = "ws" ] && [ -z "${VPN_SERVER_URL:-}" ]; then
    echo "[vpn-server] WARNING: VPN_TRANSPORT=ws, but VPN_SERVER_URL is not set."
    echo "[vpn-server] Admin host will not know which backend public host to dial over WebSocket."
fi

# ── Write server config ──────────────────────────────────────────────────────
cat > "$STATE/wg0-server.conf" <<WGEOF
[Interface]
Address = ${VPN_SERVER_IP}/24
ListenPort = ${VPN_SERVER_PORT}
PrivateKey = ${SERVER_PRIVKEY}

[Peer]
PublicKey = ${CLIENT_PUBKEY}
PresharedKey = ${PSK}
AllowedIPs = ${VPN_CLIENT_IP}/32
WGEOF

# ── Write client config (becomes the join token) ─────────────────────────────
cat > "$STATE/client.conf" <<WGEOF
# ModelLine VPN metadata. Launcher reads these comments on the admin host.
# VPN_TRANSPORT=${VPN_TRANSPORT}
# VPN_SERVER_URL=${VPN_SERVER_URL:-}
# VPN_SERVER_PORT=${VPN_SERVER_PORT}
# VPN_WS_PORT=${VPN_WS_PORT}
# VPN_WS_PATH=${VPN_WS_PATH}
# VPN_CLIENT_LOCAL_PORT=${VPN_CLIENT_LOCAL_PORT}

[Interface]
Address = ${VPN_CLIENT_IP}/32
PrivateKey = ${CLIENT_PRIVKEY}
MTU = 1420

[Peer]
PublicKey = ${SERVER_PUBKEY}
PresharedKey = ${PSK}
Endpoint = ${ENDPOINT}
AllowedIPs = ${VPN_SUBNET}
PersistentKeepalive = 25
WGEOF

# Signal the launcher that the config is ready.
touch "$STATE/.ready"

# ── Bring up wg0 ─────────────────────────────────────────────────────────────
# Remove stale interface if present (e.g. after container restart).
ip link del wg0 2>/dev/null || true

# `wg setconf` accepts the wireguard-native format, while our on-disk config
# intentionally keeps wg-quick fields like Address for operator readability.
RUNTIME_CONF="$(mktemp)"
trap 'rm -f "$RUNTIME_CONF"' EXIT
wg-quick strip "$STATE/wg0-server.conf" > "$RUNTIME_CONF"

ip link add dev wg0 type wireguard
ip address add "${VPN_SERVER_IP}/24" dev wg0
wg setconf wg0 "$RUNTIME_CONF"
ip link set wg0 up
allow_wg_firewall_input

echo "[vpn-server] WireGuard interface wg0 is UP."
echo "[vpn-server]   Server IP : ${VPN_SERVER_IP}/24"
echo "[vpn-server]   Client IP : ${VPN_CLIENT_IP}/32"
echo "[vpn-server]   UDP port  : ${VPN_SERVER_PORT}"
echo "[vpn-server]   Transport : ${VPN_TRANSPORT}"
echo "[vpn-server]   WebSocket : ${VPN_SERVER_URL:-<unset>}:${VPN_WS_PORT}/${VPN_WS_PATH}"
echo "[vpn-server]   Allowed TCP via wg0 : ${VPN_ALLOWED_TCP_PORTS}"

# Keep the container running; sleep loop avoids zombie restart loops.
while true; do sleep 30; done
