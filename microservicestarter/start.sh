#!/usr/bin/env bash
# =============================================================================
# microservicestarter — start.sh
#
# Запускает один или все микросервисы.
#
# Использование:
#   ./start.sh                                — запустить все сервисы (core)
#   ./start.sh microservice_analitic          — запустить конкретный сервис
#   ./start.sh microservice_analitic full     — core + scheduler
#   ./start.sh microservice_analitic build    — пересобрать образы и запустить
#   ./start.sh microservice_analitic logs     — live-логи
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

command -v docker >/dev/null 2>&1      || fail "docker не найден."
docker info >/dev/null 2>&1            || fail "Docker daemon не запущен."
docker compose version >/dev/null 2>&1 || fail "docker compose (v2) не найден."

# ── Реестр сервисов ──────────────────────────────────────────────────────────
declare -A SERVICE_PATHS
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    read -r svc_name svc_path <<< "$line"
    SERVICE_PATHS["$svc_name"]="$svc_path"
done < "$CONF"

# ── Первичная настройка .env с интерактивным запросом паролей ────────────────
initialize_env() {
    local name="$1"
    local svc_dir="$2"
    [[ -f "$svc_dir/.env" ]] && return
    [[ -f "$svc_dir/.env.example" ]] || { warn "[$name] .env.example не найден — пропускаем."; return; }

    info "[$name] Первый запуск — настройка .env..."
    local content
    content=$(cat "$svc_dir/.env.example")

    local pg_pass=""
    while [[ -z "$pg_pass" ]]; do
        read -rsp "[$name] Введите пароль PostgreSQL: " pg_pass; echo
        [[ -z "$pg_pass" ]] && warn "Пароль не может быть пустым."
    done

    content=$(echo "$content" | sed -E "s|^(PGPASSWORD[[:space:]]*=[[:space:]]*).*|\1$pg_pass|")
    content=$(echo "$content" | sed -E "s|^(POSTGRES_PASSWORD[[:space:]]*=[[:space:]]*).*|\1$pg_pass|")
    content=$(echo "$content" | sed "s|Password=your_strong_password_here|Password=$pg_pass|g")
    content=$(echo "$content" | sed "s|Password=your_password_here|Password=$pg_pass|g")

    echo "$content" > "$svc_dir/.env"
    success "[$name] .env создан."
}

# ── Очистка dangling-образов ──────────────────────────────────────────────────
remove_dangling_images() {
    if docker images -f "dangling=true" -q | grep -q .; then
        info "Удаляем dangling-образы Docker..."
        docker image prune -f >/dev/null
    fi
}

# ── Запуск сервиса ────────────────────────────────────────────────────────────
start_service() {
    local name="$1"
    local mode="${2:-core}"
    local path="${SERVICE_PATHS[$name]:-}"
    [[ -z "$path" ]] && fail "Сервис '$name' не найден в services.conf"
    local svc_dir="$REPO_ROOT/$path"
    [[ -d "$svc_dir" ]] || fail "Директория не найдена: $svc_dir"

    info "[$name] Запуск (mode=$mode)..."
    pushd "$svc_dir" > /dev/null

    initialize_env "$name" "$svc_dir"

    # Сборка base-образа только если compose-файл содержит сервис 'base'
    if grep -qE '^  base:' docker-compose.yml 2>/dev/null; then
        local base_tag="${name}-base:latest"
        if ! docker image inspect "$base_tag" >/dev/null 2>&1; then
            info "[$name] Сборка base-образа (первый раз, ~2 мин)..."
            docker compose --profile build-base build base || fail "[$name] Сборка base-образа провалилась."
            remove_dangling_images
            success "[$name] Base-образ готов."
        fi
    fi

    case "$mode" in
        build)
            docker compose build --no-cache
            remove_dangling_images
            docker compose up -d
            ;;
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