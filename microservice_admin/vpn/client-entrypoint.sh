#!/bin/sh
# WireGuard client entrypoint for ModelLine split deployment.
# Runs inside modelline-vpn-client container (alpine:3.19 + wireguard-tools).
# network_mode: host — wg0 appears directly on the HOST network namespace,
# so the admin-online Docker container can reach 10.44.0.1:* via host routing.
#
# State directory: /vpn/state  (mounted from .runtime-data/microservice_admin/vpn)
#   wg0.conf  — WireGuard client config written by the launcher from the join token
#   .ready    — touched after wg0 is up; launcher waits for this

set -e

STATE=/vpn/state

# Load the WireGuard kernel module if available.
modprobe wireguard 2>/dev/null || true

CONF="$STATE/wg0.conf"

if [ ! -f "$CONF" ]; then
    echo "[vpn-client] ERROR: $CONF not found." >&2
    echo "[vpn-client] Run: ./start.sh all onlyadmin <JOIN_TOKEN>" >&2
    exit 1
fi

# Extract the client Address from the config.
CLIENT_IP=$(awk '/^\[Interface\]/{p=1} p && /^Address[[:space:]]*=/{gsub(/.*=[[:space:]]*/,""); print; exit}' "$CONF")
[ -n "$CLIENT_IP" ] || CLIENT_IP="10.44.0.2/32"

# ── Bring up wg0 ─────────────────────────────────────────────────────────────
# Remove stale interface if present.
ip link del wg0 2>/dev/null || true

# The join token stores a full wg-quick style config. Strip wg-quick-only keys
# before passing it to `wg setconf`.
RUNTIME_CONF="$(mktemp)"
trap 'rm -f "$RUNTIME_CONF"' EXIT
wg-quick strip "$CONF" > "$RUNTIME_CONF"

ip link add dev wg0 type wireguard
ip address add "$CLIENT_IP" dev wg0
wg setconf wg0 "$RUNTIME_CONF"
ip link set wg0 up

echo "[vpn-client] WireGuard interface wg0 is UP."
echo "[vpn-client]   Client IP : ${CLIENT_IP}"

# Signal the launcher that the tunnel is configured.
touch "$STATE/.ready"

# Keep running.
while true; do sleep 30; done
