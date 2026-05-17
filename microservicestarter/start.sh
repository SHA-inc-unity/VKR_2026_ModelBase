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
    grep -m1 -E "^${key}=" "$env_file" 2>/dev/null \
        | sed -E "s|^${key}=||" \
        | tr -d '\r' \
        | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
        || true
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

ensure_env_file() {
    local svc_dir="$1"
    local env_file="$svc_dir/.env"
    local env_example="$svc_dir/.env.example"
    if [[ ! -f "$env_file" && -f "$env_example" ]]; then
        cp "$env_example" "$env_file"
    fi
    printf '%s' "$env_file"
}

is_valid_tcp_port() {
    local port="$1"
    [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 ))
}

is_tcp_port_listening() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|[.:])${port}$"
        return $?
    fi
    if command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|[.:])${port}$"
        return $?
    fi
    return 2
}

configure_vpn_ws_port_env() {
    local infra_env="$1"
    local current_port selected_port candidate status candidates seen_candidates=" "

    current_port="$(get_env_value "$infra_env" "VPN_WS_PORT")"
    current_port="${current_port:-8443}"
    if ! is_valid_tcp_port "$current_port"; then
        warn "[vpn-server] VPN_WS_PORT='$current_port' невалиден — используем 8443."
        current_port="8443"
    fi

    candidates="${MODELLINE_VPN_WS_PORT_CANDIDATES:-$current_port 8443 18443 28443 38443 48443 58443}"
    for candidate in $candidates; do
        [[ "$seen_candidates" == *" $candidate "* ]] && continue
        seen_candidates+="$candidate "

        if ! is_valid_tcp_port "$candidate"; then
            continue
        fi

        if is_tcp_port_listening "$candidate"; then
            warn "[vpn-server] TCP port $candidate занят на backend-host — пробуем следующий порт."
            continue
        else
            status=$?
            if [[ $status -eq 2 ]]; then
                warn "[vpn-server] Не найден ss/netstat — не могу проверить занятость VPN_WS_PORT=$current_port заранее."
                selected_port="$current_port"
                break
            fi
            selected_port="$candidate"
            break
        fi
    done

    [[ -n "${selected_port:-}" ]] || fail "[vpn-server] Все candidate-порты VPN_WS_PORT заняты: $candidates. Проверь: ss -ltnp"

    set_env_value "$infra_env" "VPN_WS_PORT" "$selected_port"
    if [[ "$selected_port" != "$current_port" ]]; then
        success "[vpn-server] VPN_WS_PORT переключён с $current_port на свободный TCP $selected_port."
    else
        success "[vpn-server] VPN_WS_PORT=$selected_port готов."
    fi
}

configure_backend_vpn_env() {
    local wg_ip="10.44.0.1"
    local infra_svc_dir infra_env
    infra_svc_dir="$(get_service_directory "microservice_infra")"
    if ! is_vpn_enabled "$infra_svc_dir"; then
        return 1
    fi

    infra_env="$(ensure_env_file "$infra_svc_dir")"
    configure_vpn_ws_port_env "$infra_env"
    set_env_value "$infra_env" "REDPANDA_EXTERNAL_HOST" "$wg_ip"
    set_env_value "$infra_env" "REDPANDA_BIND_ADDR" "$wg_ip"
    set_env_value "$infra_env" "MINIO_BIND_ADDR" "$wg_ip"

    local account_svc_dir account_env
    account_svc_dir="$(get_service_directory "microservice_account")"
    account_env="$(ensure_env_file "$account_svc_dir")"
    set_env_value "$account_env" "ACCOUNT_BIND_ADDR" "$wg_ip"

    local gateway_svc_dir gateway_env
    gateway_svc_dir="$(get_service_directory "microservice_gateway")"
    gateway_env="$(ensure_env_file "$gateway_svc_dir")"
    set_env_value "$gateway_env" "GATEWAY_BIND_ADDR" "$wg_ip"

    success "[vpn-server] Backend bind env настроены на WG IP $wg_ip."
}

validate_backend_host() {
    local backend_host="$1"
    [[ -n "$backend_host" ]] || fail "Для mode=onlyadmin backend host/IP не может быть пустым."
    [[ "$backend_host" != *"://"* ]] || fail "Для mode=onlyadmin указывай только host/IP без схемы: $backend_host"
    [[ "$backend_host" != */* ]] || fail "Для mode=onlyadmin указывай только host/IP без пути: $backend_host"
    [[ ! "$backend_host" =~ [[:space:]] ]] || fail "Для mode=onlyadmin host/IP не должен содержать пробелы: $backend_host"
}

resolve_admin_online_backend_host() {
    local svc_dir="$1"
    local explicit_backend_host="${2:-}"
    local env_file="$svc_dir/.env"
    local env_example="$svc_dir/.env.example"

    if [[ ! -f "$env_file" ]]; then
        [[ -f "$env_example" ]] || fail "[microservice_admin] .env.example не найден — не можем настроить admin-online."
        cp "$env_example" "$env_file"
    fi

    local current_backend_host backend_host=""
    current_backend_host="$(get_env_value "$env_file" "ONLINE_BACKEND_HOST")"
    explicit_backend_host="$(printf '%s' "$explicit_backend_host" | tr -d '\r' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"

    if [[ -n "$explicit_backend_host" ]]; then
        backend_host="$explicit_backend_host"
    elif [[ -n "$current_backend_host" ]]; then
        if [[ -t 0 ]]; then
            info "[microservice_admin] Текущий backend host/IP для admin-online: $current_backend_host" >&2
            read -rp "[microservice_admin] Введите backend host/IP для admin-online [$current_backend_host]: " backend_host
            backend_host="${backend_host:-$current_backend_host}"
        else
            backend_host="$current_backend_host"
        fi
    elif [[ -t 0 ]]; then
        info "[microservice_admin] ONLINE_BACKEND_HOST не задан — сейчас запросим backend host/IP для admin-online." >&2
        while [[ -z "$backend_host" ]]; do
            read -rp "[microservice_admin] Введите backend host/IP для admin-online: " backend_host
            [[ -z "$backend_host" ]] && warn "Backend host/IP не может быть пустым." >&2
        done
    else
        fail "Для mode=onlyadmin укажи backend host/IP третьим аргументом или сохрани ONLINE_BACKEND_HOST в microservice_admin/.env"
    fi

    backend_host="$(printf '%s' "$backend_host" | tr -d '\r' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    validate_backend_host "$backend_host"
    printf '%s' "$backend_host"
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

    local backend_host
    backend_host="$(resolve_admin_online_backend_host "$svc_dir" "$explicit_backend_host")"

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

# ── Containerized VPN helpers ─────────────────────────────────────────────────
# Returns 0 (true) if VPN_SERVER_URL is set in microservice_infra/.env (or .env.example).
# Used to activate the "vpn" compose profile in noadmin mode.
is_vpn_enabled() {
    local svc_dir="$1"
    local env_file="$svc_dir/.env"
    local env_example="$svc_dir/.env.example"
    # Ensure .env exists so get_env_value works on first run.
    if [[ ! -f "$env_file" ]]; then
        [[ -f "$env_example" ]] || return 1
        cp "$env_example" "$env_file"
    fi
    local server_url
    server_url="$(get_env_value "$env_file" "VPN_SERVER_URL")"
    [[ -n "$server_url" ]]
}

# Returns 0 (true) if the argument looks like a base64-encoded WireGuard config
# (i.e. it decodes to text that starts with "[Interface]").
# Used to distinguish a VPN join token from a plain backend host/IP.
is_vpn_join_token() {
    local arg="$1"
    [[ -n "$arg" ]] || return 1
    printf '%s' "$arg" | base64 -d 2>/dev/null | grep -q '^\[Interface\]'
}

# Waits for the VPN server to generate the join token, then prints it.
# Called after microservice_infra starts in noadmin+vpn mode.
print_vpn_join_token() {
    local state_dir="$REPO_ROOT/.runtime-data/microservice_infra/vpn"
    info "[vpn-server] Ожидаем генерации ключей WireGuard..."
    local attempts=0
    while [[ ! -f "$state_dir/.ready" ]]; do
        [[ $attempts -lt 60 ]] || fail "[vpn-server] Таймаут: join token не сгенерирован за 120 с."
        sleep 2
        attempts=$((attempts + 1))
    done

    local client_conf="$state_dir/client.conf"
    [[ -f "$client_conf" ]] || fail "[vpn-server] client.conf не найден — что-то пошло не так."

    local join_token
    # base64 -w 0 (GNU) or base64 (macOS/BSD, outputs without newlines on single pass)
    join_token="$(base64 -w 0 "$client_conf" 2>/dev/null || base64 "$client_conf")"

    local server_ip
    server_ip="$(grep -m1 'Address' "$state_dir/wg0-server.conf" 2>/dev/null \
                 | sed -E 's|.*=[[:space:]]*||; s|/.*||' || true)"
    local ws_port
    ws_port="$(get_wg_conf_meta "$client_conf" "VPN_WS_PORT")"

    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║         VPN JOIN TOKEN — скопируй на admin-хост                 ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║  Backend WG IP : ${server_ip:-10.44.0.1}                                  ║"
    echo "║  WebSocket TCP : ${ws_port:-8443}                                      ║"
    echo "║                                                                  ║"
    echo "║  На admin-хосте запусти:                                         ║"
    echo "║  ./start.sh all onlyadmin <JOIN_TOKEN>                           ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "$join_token"
    echo ""
}

get_wg_conf_meta() {
    local conf_file="$1"
    local key="$2"
    grep -m1 -E "^# ${key}=" "$conf_file" 2>/dev/null \
        | sed -E "s|^# ${key}=||" \
        | tr -d '\r' \
    | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
    || true
}

configure_vpn_transport_env_from_conf() {
    local admin_svc_dir="$1"
    local state_dir="$REPO_ROOT/.runtime-data/microservice_admin/vpn"
    local conf_file="$state_dir/wg0.conf"
    [[ -f "$conf_file" ]] || return 0

    local env_file="$admin_svc_dir/.env"
    local env_example="$admin_svc_dir/.env.example"
    if [[ ! -f "$env_file" && -f "$env_example" ]]; then
        cp "$env_example" "$env_file"
    fi

    local server_url server_port ws_port ws_path client_local_port endpoint endpoint_host endpoint_port
    server_url="$(get_wg_conf_meta "$conf_file" "VPN_SERVER_URL")"
    server_port="$(get_wg_conf_meta "$conf_file" "VPN_SERVER_PORT")"
    ws_port="$(get_wg_conf_meta "$conf_file" "VPN_WS_PORT")"
    ws_path="$(get_wg_conf_meta "$conf_file" "VPN_WS_PATH")"
    client_local_port="$(get_wg_conf_meta "$conf_file" "VPN_CLIENT_LOCAL_PORT")"

    server_port="${server_port:-51820}"
    ws_port="${ws_port:-8443}"
    ws_path="${ws_path:-modelline-wg}"
    client_local_port="${client_local_port:-51820}"

    endpoint="$(awk '/^\[Peer\]/{p=1} p && /^Endpoint[[:space:]]*=/{gsub(/.*=[[:space:]]*/,""); print; exit}' "$conf_file" | tr -d '\r')"
    if [[ -z "$server_url" && -n "$endpoint" && "$endpoint" != 127.0.0.1:* && "$endpoint" != localhost:* && "$endpoint" != \<* ]]; then
        endpoint_host="${endpoint%:*}"
        endpoint_port="${endpoint##*:}"
        server_url="$endpoint_host"
        [[ -n "$endpoint_port" && "$endpoint_port" != "$endpoint_host" ]] && server_port="$endpoint_port"
    fi

    [[ -n "$server_url" ]] && set_env_value "$env_file" "VPN_SERVER_URL" "$server_url"
    set_env_value "$env_file" "VPN_SERVER_PORT" "$server_port"
    set_env_value "$env_file" "VPN_WS_PORT" "$ws_port"
    set_env_value "$env_file" "VPN_WS_PATH" "$ws_path"
    set_env_value "$env_file" "VPN_CLIENT_LOCAL_PORT" "$client_local_port"

    if [[ -n "$server_url" && "$endpoint" != "127.0.0.1:$client_local_port" ]]; then
        sed -i.bak -E "s|^Endpoint[[:space:]]*=.*$|Endpoint = 127.0.0.1:${client_local_port}|" "$conf_file"
        rm -f "$conf_file.bak"
    fi

    success "[vpn-client] WebSocket transport настроен: ${server_url:-<unset>}:${ws_port}/${ws_path} -> 127.0.0.1:${client_local_port}."
}

# Decodes a join token into wg0.conf and creates the VPN state directory.
setup_vpn_client() {
    local admin_svc_dir="$1"
    local join_token="$2"
    local state_dir="$REPO_ROOT/.runtime-data/microservice_admin/vpn"
    mkdir -p "$state_dir"

    printf '%s' "$join_token" | base64 -d > "$state_dir/wg0.conf" 2>/dev/null \
        || fail "[vpn-client] Невозможно декодировать join token."
    grep -q '^\[Interface\]' "$state_dir/wg0.conf" \
        || fail "[vpn-client] Декодированный join token — не валидный WireGuard config."

    configure_vpn_transport_env_from_conf "$admin_svc_dir"

    success "[vpn-client] wg0.conf записан в $state_dir"
}

# Waits for vpn-client to write .ready marker (wg0 is up on the host).
wait_vpn_client() {
    local state_dir="$REPO_ROOT/.runtime-data/microservice_admin/vpn"
    info "[vpn-client] Ожидаем подъёма WireGuard интерфейса wg0..."
    local attempts=0
    while [[ ! -f "$state_dir/.ready" ]]; do
        [[ $attempts -lt 60 ]] || fail "[vpn-client] Таймаут: wg0 не поднялся за 120 с."
        sleep 2
        attempts=$((attempts + 1))
    done
    success "[vpn-client] WireGuard tunnel активен."
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
        core|"")
            if [[ "$name" == "microservice_infra" ]] && is_vpn_enabled "$svc_dir"; then
                docker compose --profile vpn up -d
            else
                docker compose up -d
            fi
            ;;
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
    admin_svc_dir="$(get_service_directory "microservice_admin")"

    if is_vpn_join_token "${BACKEND_HOST:-}"; then
        # ── VPN join token provided — set up containerized WireGuard client ──
        VPN_JOIN_TOKEN="$BACKEND_HOST"
        BACKEND_HOST="10.44.0.1"   # fixed backend WG IP
        setup_vpn_client "$admin_svc_dir" "$VPN_JOIN_TOKEN"
        # Remove stale .ready marker so wait_vpn_client waits for the new run.
        rm -f "$REPO_ROOT/.runtime-data/microservice_admin/vpn/.ready"
        pushd "$admin_svc_dir" > /dev/null
        initialize_env "microservice_admin" "$admin_svc_dir"
        docker compose --profile vpn up -d --force-recreate wstunnel-client vpn-client
        popd > /dev/null
        wait_vpn_client
    elif [[ -f "$REPO_ROOT/.runtime-data/microservice_admin/vpn/wg0.conf" ]]; then
        # ── Existing VPN config — restart vpn-client and reuse WG IP ─────────
        info "[vpn-client] Найден wg0.conf из предыдущего запуска — переподнимаем VPN туннель."
        BACKEND_HOST="10.44.0.1"
        rm -f "$REPO_ROOT/.runtime-data/microservice_admin/vpn/.ready"
        pushd "$admin_svc_dir" > /dev/null
        initialize_env "microservice_admin" "$admin_svc_dir"
        configure_vpn_transport_env_from_conf "$admin_svc_dir"
        docker compose --profile vpn up -d --force-recreate wstunnel-client vpn-client
        popd > /dev/null
        wait_vpn_client
    else
        BACKEND_HOST="$(resolve_admin_online_backend_host "$admin_svc_dir" "${BACKEND_HOST:-}")"
    fi

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
            if [[ "$MODE" == "noadmin" ]]; then
                configure_backend_vpn_env || true
            fi
            start_service "microservice_infra" "$dispatch_mode"

            # In noadmin+vpn mode: print join token after infra/VPN startup.
            if [[ "$MODE" == "noadmin" ]]; then
                infra_svc_dir="$(get_service_directory "microservice_infra")"
                if is_vpn_enabled "$infra_svc_dir"; then
                    print_vpn_join_token
                fi
            fi

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