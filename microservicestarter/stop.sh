#!/usr/bin/env bash
# =============================================================================
# microservicestarter — stop.sh
#
# Останавливает один или все микросервисы.
#
# Использование:
#   ./stop.sh                               — остановить все сервисы
#   ./stop.sh microservice_analitic         — остановить конкретный сервис
#   ./stop.sh all                           — остановить все сервисы
#   ./stop.sh microservice_analitic clean   — остановить + удалить volumes (СБРОС БД!)
#   ./stop.sh microservice_analitic prune   — остановить + удалить образы сервиса
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONF="$SCRIPT_DIR/services.conf"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[starter]${NC} $*"; }
success() { echo -e "${GREEN}[starter]${NC} $*"; }
warn()    { echo -e "${YELLOW}[starter]${NC} $*"; }
fail()    { echo -e "${RED}[starter] ERROR:${NC} $*"; exit 1; }

command -v docker >/dev/null 2>&1       || fail "docker не найден."
docker info >/dev/null 2>&1             || fail "Docker daemon не запущен."
docker compose version >/dev/null 2>&1  || fail "docker compose (v2) не найден."

declare -A SERVICE_PATHS
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    read -r svc_name svc_path <<< "$line"
    SERVICE_PATHS["$svc_name"]="$svc_path"
done < "$CONF"

stop_service() {
    local name="$1"
    local mode="${2:-stop}"
    local path="${SERVICE_PATHS[$name]:-}"
    [[ -z "$path" ]] && fail "Сервис '$name' не найден в services.conf"
    local svc_dir="$REPO_ROOT/$path"
    [[ -d "$svc_dir" ]] || fail "Директория не найдена: $svc_dir"

    info "[$name] Остановка (mode=$mode)..."
    pushd "$svc_dir" > /dev/null

    case "$mode" in
        clean)
            warn "[$name] ВНИМАНИЕ: будут удалены все volumes (БД, модели)!"
            read -rp "Подтвердите (yes/no): " CONFIRM
            [[ "$CONFIRM" == "yes" ]] || { echo "Отменено."; popd > /dev/null; return; }
            docker compose --profile scheduler down --volumes --remove-orphans
            success "[$name] Остановлен, volumes удалены."
            ;;
        prune)
            docker compose --profile scheduler down --rmi local --remove-orphans
            success "[$name] Остановлен, образы удалены."
            ;;
        stop|"")
            docker compose --profile scheduler down --remove-orphans
            success "[$name] Остановлен."
            ;;
        *)
            fail "[$name] Неизвестный режим: $mode"
            ;;
    esac

    popd > /dev/null
}

TARGET="${1:-all}"
MODE="${2:-stop}"

if [[ "$TARGET" == "all" ]]; then
    for svc in "${!SERVICE_PATHS[@]}"; do stop_service "$svc" "$MODE"; done
else
    stop_service "$TARGET" "$MODE"
fi