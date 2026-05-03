#!/usr/bin/env bash
# =============================================================================
# microservicestarter — start.sh
#
# Запускает один или все микросервисы.
#
# Использование:
#   ./start.sh                                — запустить все сервисы (core)
#   ./start.sh all noadmin                    — запустить всё, кроме admin
#   ./start.sh all onlyadmin                  — запустить только admin-head
#   ./start.sh microservice_analitic          — запустить конкретный сервис
#   ./start.sh microservice_admin onlyadmin   — запустить только admin-head
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
declare -a SERVICE_ORDER
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    read -r svc_name svc_path <<< "$line"
    SERVICE_PATHS["$svc_name"]="$svc_path"
    SERVICE_ORDER+=("$svc_name")
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

get_service_directory() {
    local name="$1"
    local path="${SERVICE_PATHS[$name]:-}"
    [[ -n "$path" ]] || fail "Сервис '$name' не найден в services.conf"
    local svc_dir="$REPO_ROOT/$path"
    [[ -d "$svc_dir" ]] || fail "Директория не найдена: $svc_dir"
    echo "$svc_dir"
}

prepare_start_selection() {
    local name svc_dir
    for name in "$@"; do
        svc_dir="$(get_service_directory "$name")"
        initialize_env "$name" "$svc_dir"
    done
}

run_parallel_start_selection() {
    local mode="$1"
    shift
    local services=("$@")
    [[ ${#services[@]} -gt 0 ]] || return 0

    info "Параллельный запуск: ${services[*]}"
    local -a pids=()
    local -a names=()
    local svc
    for svc in "${services[@]}"; do
        "$SCRIPT_DIR/start.sh" "$svc" "$mode" &
        pids+=("$!")
        names+=("$svc")
    done

    local failed=0
    local idx
    for idx in "${!pids[@]}"; do
        if wait "${pids[$idx]}"; then
            success "[${names[$idx]}] Параллельный запуск завершён."
        else
            warn "[${names[$idx]}] Параллельный запуск завершился с ошибкой."
            failed=1
        fi
    done

    [[ $failed -eq 0 ]] || fail "Параллельный запуск завершился с ошибкой."
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
        onlyadmin)
            [[ "$name" == "microservice_admin" ]] || fail "[$name] mode=onlyadmin поддерживается только для microservice_admin"
            docker compose --profile online up -d --build admin-online
            ;;
        core|"")   docker compose up -d ;;
        *)         fail "[$name] Неизвестный режим: $mode" ;;
    esac

    popd > /dev/null
    success "[$name] Запущен."
}

TARGET="${1:-all}"
MODE="${2:-core}"

if [[ "$MODE" == "onlyadmin" ]]; then
    [[ "$TARGET" == "all" || "$TARGET" == "microservice_admin" ]] || fail "mode=onlyadmin поддерживается только для microservice_admin"
    start_service "microservice_admin" "onlyadmin"
else
    declare -a selected_services=()
    dispatch_mode="$MODE"

    if [[ "$MODE" == "noadmin" ]]; then
        [[ "$TARGET" == "all" ]] || fail "mode=noadmin поддерживается только вместе с target=all"
        dispatch_mode="core"
        for svc in "${SERVICE_ORDER[@]}"; do
            [[ "$svc" == "microservice_admin" ]] && continue
            selected_services+=("$svc")
        done
    elif [[ "$TARGET" == "all" ]]; then
        selected_services=("${SERVICE_ORDER[@]}")
    else
        selected_services+=("$TARGET")
    fi

    if [[ ${#selected_services[@]} -gt 1 && "$dispatch_mode" != "logs" ]]; then
        if [[ " ${selected_services[*]} " == *" microservice_infra "* ]]; then
            start_service "microservice_infra" "$dispatch_mode"
            filtered_services=()
            for svc in "${selected_services[@]}"; do
                [[ "$svc" == "microservice_infra" ]] || filtered_services+=("$svc")
            done
            selected_services=("${filtered_services[@]}")
        fi

        if [[ ${#selected_services[@]} -gt 0 ]]; then
            prepare_start_selection "${selected_services[@]}"
        fi

        if [[ ${#selected_services[@]} -gt 1 ]]; then
            run_parallel_start_selection "$dispatch_mode" "${selected_services[@]}"
        elif [[ ${#selected_services[@]} -eq 1 ]]; then
            start_service "${selected_services[0]}" "$dispatch_mode"
        fi
    else
        for svc in "${selected_services[@]}"; do
            start_service "$svc" "$dispatch_mode"
        done
    fi
fi