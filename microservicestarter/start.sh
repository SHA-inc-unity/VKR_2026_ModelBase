#!/usr/bin/env bash
# =============================================================================
# microservicestarter — start.sh
#
# Запускает один или все микросервисы.
#
# Использование:
#   ./start.sh                               — запустить все сервисы (core)
#   ./start.sh microservice_analitic         — запустить конкретный сервис
#   ./start.sh all                           — запустить все сервисы
#   ./start.sh microservice_analitic full    — core + scheduler
#   ./start.sh microservice_analitic build   — пересобрать образы и запустить
#   ./start.sh microservice_analitic logs    — live-логи
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

# Загружаем реестр сервисов
declare -A SERVICE_PATHS
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    read -r svc_name svc_path <<< "$line"
    SERVICE_PATHS["$svc_name"]="$svc_path"
done < "$CONF"

start_service() {
    local name="$1"
    local mode="${2:-core}"
    local path="${SERVICE_PATHS[$name]:-}"
    [[ -z "$path" ]] && fail "Сервис '$name' не найден в services.conf"
    local svc_dir="$REPO_ROOT/$path"
    [[ -d "$svc_dir" ]] || fail "Директория не найдена: $svc_dir"

    info "[$name] Запуск (mode=$mode)..."
    pushd "$svc_dir" > /dev/null

    if [[ ! -f .env && -f .env.example ]]; then
        cp .env.example .env
        warn "[$name] .env создан из .env.example. Укажите PGPASSWORD."
    fi

    local base_tag="${name}-base:latest"
    if ! docker image inspect "$base_tag" >/dev/null 2>&1; then
        info "[$name] Сборка base-образа (первый раз, ~2 мин)..."
        docker compose --profile build-base build base || fail "[$name] Сборка base-образа провалилась."
        success "[$name] Base-образ готов."
    fi

    case "$mode" in
        build)     docker compose build --no-cache ; docker compose up -d ;;
        full)      docker compose --profile scheduler up -d ;;
        scheduler) docker compose --profile scheduler up -d scheduler ;;
        logs)      docker compose logs -f ;;
        core|"")   docker compose up -d ;;
        *)         fail "[$name] Неизвестный режим: $mode" ;;
    esac

    popd > /dev/null
    success "[$name] Запущен."
}

TARGET="${1:-all}"
MODE="${2:-core}"

if [[ "$TARGET" == "all" ]]; then
    for svc in "${!SERVICE_PATHS[@]}"; do start_service "$svc" "$MODE"; done
else
    start_service "$TARGET" "$MODE"
fi