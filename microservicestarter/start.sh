#!/usr/bin/env bash
# =============================================================================
# microservicestarter — start.sh
#
# Запускает один или все микросервисы.
#
# Использование:
#   ./start.sh                                — запустить все сервисы (core)
#   ./start.sh all noadmin                    — запустить всё, кроме admin
#   ./start.sh all onlyadmin [backend-host]   — запустить только admin-head
#   ./start.sh microservice_analitic          — запустить конкретный сервис
#   ./start.sh microservice_admin onlyadmin [backend-host] — запустить только admin-head
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

    if grep -qE '(^PGPASSWORD[[:space:]]*=|^POSTGRES_PASSWORD[[:space:]]*=|Password=your_strong_password_here|Password=your_password_here)' <<< "$content"; then
        local pg_pass=""
        while [[ -z "$pg_pass" ]]; do
            read -rsp "[$name] Введите пароль PostgreSQL: " pg_pass; echo
            [[ -z "$pg_pass" ]] && warn "Пароль не может быть пустым."
        done

        content=$(echo "$content" | sed -E "s|^(PGPASSWORD[[:space:]]*=[[:space:]]*).*|\1$pg_pass|")
        content=$(echo "$content" | sed -E "s|^(POSTGRES_PASSWORD[[:space:]]*=[[:space:]]*).*|\1$pg_pass|")
        content=$(echo "$content" | sed "s|Password=your_strong_password_here|Password=$pg_pass|g")
        content=$(echo "$content" | sed "s|Password=your_password_here|Password=$pg_pass|g")
    fi

    echo "$content" > "$svc_dir/.env"
    success "[$name] .env создан."
}

get_env_value() {
    local env_file="$1"
    local key="$2"
    [[ -f "$env_file" ]] || return 0
    grep -m1 -E "^${key}=" "$env_file" | sed -E "s|^${key}=||"
}

set_env_value() {
    local env_file="$1"
    local key="$2"
    local value="$3"
    if grep -q -E "^${key}=" "$env_file" 2>/dev/null; then
        sed -i.bak -E "s|^${key}=.*$|${key}=${value}|" "$env_file"
        rm -f "$env_file.bak"
    else
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

validate_backend_host() {
    local backend_host="$1"
    [[ -n "$backend_host" ]] || fail "Для mode=onlyadmin backend host/IP не может быть пустым."
    [[ "$backend_host" != *"://"* ]] || fail "Для mode=onlyadmin указывай только host/IP без схемы: $backend_host"
    [[ "$backend_host" != */* ]] || fail "Для mode=onlyadmin указывай только host/IP без пути: $backend_host"
    [[ ! "$backend_host" =~ [[:space:]] ]] || fail "Для mode=onlyadmin host/IP не должен содержать пробелы: $backend_host"
}

configure_admin_online_env() {
    local svc_dir="$1"
    local explicit_backend_host="${2:-}"
    local env_file="$svc_dir/.env"
    local env_example="$svc_dir/.env.example"

    if [[ ! -f "$env_file" ]]; then
        [[ -f "$env_example" ]] || fail "[microservice_admin] .env.example не найден — не можем настроить admin-online."
        cp "$env_example" "$env_file"
    fi

    local current_backend_host backend_host
    current_backend_host="$(get_env_value "$env_file" "ONLINE_BACKEND_HOST")"

    if [[ -n "$explicit_backend_host" ]]; then
        backend_host="$explicit_backend_host"
    elif [[ -t 0 ]]; then
        if [[ -n "$current_backend_host" ]]; then
            read -rp "[microservice_admin] Backend host/IP для admin-online [$current_backend_host]: " backend_host
            backend_host="${backend_host:-$current_backend_host}"
        else
            while [[ -z "$backend_host" ]]; do
                read -rp "[microservice_admin] Backend host/IP для admin-online: " backend_host
                [[ -z "$backend_host" ]] && warn "Backend host/IP не может быть пустым."
            done
        fi
    elif [[ -n "$current_backend_host" ]]; then
        backend_host="$current_backend_host"
    else
        fail "Для mode=onlyadmin укажи backend host/IP третьим аргументом или сохрани ONLINE_BACKEND_HOST в microservice_admin/.env"
    fi

    validate_backend_host "$backend_host"

    set_env_value "$env_file" "ONLINE_BACKEND_HOST" "$backend_host"
    set_env_value "$env_file" "ONLINE_KAFKA_BOOTSTRAP_SERVERS" "$backend_host:9092"
    set_env_value "$env_file" "ONLINE_REDPANDA_ADMIN_URL" "$backend_host:9644"
    set_env_value "$env_file" "ONLINE_ACCOUNT_URL" "$backend_host:7510"
    set_env_value "$env_file" "ONLINE_GATEWAY_URL" "$backend_host:7520"
    set_env_value "$env_file" "ONLINE_MINIO_URL" "$backend_host:9000"

    success "[microservice_admin] ONLINE_* настроены на backend-host $backend_host."
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

get_bind_mount_data_paths() {
    case "$1" in
        microservice_account)
            printf '%s\n' "$REPO_ROOT/.runtime-data/microservice_account/postgres"
            printf '%s\n' "$REPO_ROOT/.runtime-data/microservice_account/redis"
            ;;
        microservice_data)
            printf '%s\n' "$REPO_ROOT/.runtime-data/microservice_data/postgres"
            ;;
        microservice_analitic)
            printf '%s\n' "$REPO_ROOT/.runtime-data/microservice_analitic/redis"
            printf '%s\n' "$REPO_ROOT/.runtime-data/microservice_analitic/models"
            ;;
        microservice_infra)
            printf '%s\n' "$REPO_ROOT/.runtime-data/microservice_infra/redpanda"
            printf '%s\n' "$REPO_ROOT/.runtime-data/microservice_infra/minio"
            ;;
    esac
}

prepare_bind_mount_data_paths() {
    local name="$1"
    while IFS= read -r data_path; do
        [[ -z "$data_path" ]] && continue
        mkdir -p "$data_path"
        chmod -R a+rwX "$data_path" 2>/dev/null || true
    done < <(get_bind_mount_data_paths "$name")
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
        bash "$SCRIPT_DIR/start.sh" "$svc" "$mode" &
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
    prepare_bind_mount_data_paths "$name"

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
            configure_admin_online_env "$svc_dir" "${BACKEND_HOST:-}"
            docker compose --profile online up -d admin-online
            ;;
        core|"")   docker compose up -d ;;
        *)         fail "[$name] Неизвестный режим: $mode" ;;
    esac

    popd > /dev/null
    success "[$name] Запущен."
}

TARGET="${1:-all}"
MODE="${2:-core}"
BACKEND_HOST="${3:-}"

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