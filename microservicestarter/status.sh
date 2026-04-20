#!/usr/bin/env bash
# =============================================================================
# microservicestarter — status.sh
#
# Показывает состояние контейнеров для всех или выбранного сервиса.
#
# Использование:
#   ./status.sh                               — статус всех сервисов
#   ./status.sh microservice_analitic         — статус конкретного сервиса
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONF="$SCRIPT_DIR/services.conf"

CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}[starter]${NC} $*"; }

command -v docker >/dev/null 2>&1 || { echo "docker не найден."; exit 1; }

declare -A SERVICE_PATHS
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    read -r svc_name svc_path <<< "$line"
    SERVICE_PATHS["$svc_name"]="$svc_path"
done < "$CONF"

show_status() {
    local name="$1"
    local path="${SERVICE_PATHS[$name]:-}"
    [[ -z "$path" ]] && echo "Сервис '$name' не найден в services.conf" && return
    local svc_dir="$REPO_ROOT/$path"
    [[ -d "$svc_dir" ]] || { echo "[$name] Директория не найдена: $svc_dir"; return; }

    info "[$name] Состояние контейнеров:"
    pushd "$svc_dir" > /dev/null
    docker compose ps
    popd > /dev/null
}

TARGET="${1:-all}"

if [[ "$TARGET" == "all" ]]; then
    for svc in "${!SERVICE_PATHS[@]}"; do show_status "$svc"; done
else
    show_status "$TARGET"
fi