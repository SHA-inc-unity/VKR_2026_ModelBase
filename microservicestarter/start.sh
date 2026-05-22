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

get_env_value_or_default() {
    local env_file="$1"
    local key="$2"
    local default_value="$3"
    local current_value

    current_value="$(get_env_value "$env_file" "$key")"
    if [[ -n "$current_value" ]]; then
        printf '%s' "$current_value"
    else
        printf '%s' "$default_value"
    fi
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

is_wildcard_bind_addr() {
    local bind_addr="${1:-}"
    [[ -z "$bind_addr" || "$bind_addr" == "0.0.0.0" || "$bind_addr" == "::" || "$bind_addr" == "[::]" ]]
}

compose_project_owns_container() {
    local container_id="$1"
    local compose_container_id

    while IFS= read -r compose_container_id; do
        [[ -z "$compose_container_id" ]] && continue
        [[ "$compose_container_id" == "$container_id" ]] && return 0
        [[ "$compose_container_id" == "$container_id"* ]] && return 0
        [[ "$container_id" == "$compose_container_id"* ]] && return 0
    done < <(docker compose ps -q 2>/dev/null || true)

    return 1
}

get_docker_container_using_host_port() {
    local port="$1"

    docker ps --no-trunc --format '{{.ID}}\t{{.Names}}\t{{.Ports}}' | awk -F '\t' -v port="$port" '
        function published_port_matches(entry, port, host_part, arrow_pos, dash_pos, start_port, end_port) {
            arrow_pos = index(entry, "->")
            if (arrow_pos == 0) {
                return 0
            }

            host_part = substr(entry, 1, arrow_pos - 1)
            sub(/^.*:/, "", host_part)

            if (host_part == port) {
                return 1
            }

            if (host_part ~ /^[0-9]+-[0-9]+$/) {
                dash_pos = index(host_part, "-")
                start_port = substr(host_part, 1, dash_pos - 1) + 0
                end_port = substr(host_part, dash_pos + 1) + 0
                return (port + 0) >= start_port && (port + 0) <= end_port
            }

            return 0
        }

        {
            count = split($3, port_entries, /, /)
            for (i = 1; i <= count; ++i) {
                if (published_port_matches(port_entries[i], port)) {
                    print $1 "\t" $2
                    exit
                }
            }
        }
    '
}

get_non_docker_listener_using_host_port() {
    local port="$1"
    local listener_info=""

    if command -v ss >/dev/null 2>&1; then
        listener_info="$(ss -ltnp "( sport = :$port )" 2>/dev/null | awk 'NR==2 { $1=$1; print; exit }')"
        if [[ -n "$listener_info" ]]; then
            printf '%s' "$listener_info"
            return 0
        fi
    fi

    if command -v lsof >/dev/null 2>&1; then
        listener_info="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 { print $1 " pid=" $2; exit }')"
        if [[ -n "$listener_info" ]]; then
            printf '%s' "$listener_info"
            return 0
        fi
    fi

    return 1
}

require_host_port_available() {
    local service_name="$1"
    local env_file="$2"
    local env_key="$3"
    local bind_addr="$4"
    local port="$5"
    local purpose="$6"
    local owner_info owner_id owner_name listener_info

    [[ -n "$port" ]] || return 0
    is_wildcard_bind_addr "$bind_addr" || return 0

    owner_info="$(get_docker_container_using_host_port "$port")"
    if [[ -n "$owner_info" ]]; then
        owner_id="${owner_info%%$'\t'*}"
        owner_name="${owner_info#*$'\t'}"
        if ! compose_project_owns_container "$owner_id"; then
            fail "[$service_name] Host-порт $port для $purpose уже занят контейнером '$owner_name'. stop.sh останавливает только compose-проекты ModelLine; останови/перенастрой внешний контейнер или измени $env_key в $env_file"
        fi
        return 0
    fi

    listener_info="$(get_non_docker_listener_using_host_port "$port" || true)"
    if [[ -n "$listener_info" ]]; then
        fail "[$service_name] Host-порт $port для $purpose уже занят другим процессом ($listener_info). Освободи порт или измени $env_key в $env_file"
    fi
}

preflight_check_host_ports() {
    local service_name="$1"
    local mode="$2"
    local svc_dir="$3"
    local env_file="$svc_dir/.env"

    case "$service_name" in
        microservice_infra)
            require_host_port_available "$service_name" "$env_file" "REDPANDA_EXTERNAL_PORT" "$(get_env_value_or_default "$env_file" "REDPANDA_BIND_ADDR" "")" "$(get_env_value_or_default "$env_file" "REDPANDA_EXTERNAL_PORT" "9092")" "Redpanda Kafka external listener"
            require_host_port_available "$service_name" "$env_file" "REDPANDA_ADMIN_PORT" "$(get_env_value_or_default "$env_file" "REDPANDA_BIND_ADDR" "")" "$(get_env_value_or_default "$env_file" "REDPANDA_ADMIN_PORT" "9644")" "Redpanda admin API"
            require_host_port_available "$service_name" "$env_file" "REDPANDA_CONSOLE_PORT" "" "$(get_env_value_or_default "$env_file" "REDPANDA_CONSOLE_PORT" "8080")" "Redpanda Console"
            require_host_port_available "$service_name" "$env_file" "MINIO_API_PORT" "$(get_env_value_or_default "$env_file" "MINIO_BIND_ADDR" "")" "$(get_env_value_or_default "$env_file" "MINIO_API_PORT" "9000")" "MinIO API"
            require_host_port_available "$service_name" "$env_file" "MINIO_CONSOLE_PORT" "" "$(get_env_value_or_default "$env_file" "MINIO_CONSOLE_PORT" "9001")" "MinIO Console"
            require_host_port_available "$service_name" "$env_file" "NGINX_PORT" "" "$(get_env_value_or_default "$env_file" "NGINX_PORT" "8501")" "infra nginx ingress"
            require_host_port_available "$service_name" "$env_file" "ADMIN_BACKEND_PORT" "" "$(get_env_value_or_default "$env_file" "ADMIN_BACKEND_PORT" "8443")" "backend HTTPS admin facade"
            ;;
        microservice_account)
            require_host_port_available "$service_name" "$env_file" "ACCOUNT_API_PORT" "$(get_env_value_or_default "$env_file" "ACCOUNT_BIND_ADDR" "")" "$(get_env_value_or_default "$env_file" "ACCOUNT_API_PORT" "7510")" "Account API"
            ;;
        microservice_gateway)
            require_host_port_available "$service_name" "$env_file" "GATEWAY_API_PORT" "$(get_env_value_or_default "$env_file" "GATEWAY_BIND_ADDR" "")" "$(get_env_value_or_default "$env_file" "GATEWAY_API_PORT" "7520")" "Gateway API"
            ;;
        microservice_admin)
            if [[ "$mode" == "onlyadmin" ]]; then
                require_host_port_available "$service_name" "$env_file" "ADMIN_HTTP_PORT" "" "$(get_env_value_or_default "$env_file" "ADMIN_HTTP_PORT" "80")" "admin-online HTTP redirect entrypoint"
                require_host_port_available "$service_name" "$env_file" "ADMIN_HTTPS_PORT" "" "$(get_env_value_or_default "$env_file" "ADMIN_HTTPS_PORT" "443")" "admin-online HTTPS entrypoint"
            fi
            ;;
    esac
}

validate_backend_host() {
    local backend_host="$1"
    [[ -n "$backend_host" ]] || fail "Для mode=onlyadmin backend host/IP не может быть пустым."
    [[ "$backend_host" != *"://"* ]] || fail "Для mode=onlyadmin указывай только host/IP без схемы: $backend_host"
    [[ "$backend_host" != */* ]] || fail "Для mode=onlyadmin указывай только host/IP без пути: $backend_host"
    [[ ! "$backend_host" =~ [[:space:]] ]] || fail "Для mode=onlyadmin host/IP не должен содержать пробелы: $backend_host"
}

validate_backend_base_url() {
    local base_url="$1"
    [[ -n "$base_url" ]] || fail "Base URL backend-фасада не может быть пустым."
    [[ ! "$base_url" =~ [[:space:]] ]] || fail "Base URL backend-фасада не должен содержать пробелы: $base_url"
    [[ "$base_url" =~ ^https?://[^/]+/?$ ]] || fail "Ожидается base URL без пути, например https://backend.example.com:8443"
}

extract_http_url_scheme() {
    local url="$1"
    if [[ "$url" =~ ^(https?):// ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    fi
}

extract_http_url_port() {
    local url="$1"
    if [[ "$url" =~ ^https?://[^/:]+:([0-9]+)/?$ ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    fi
}

infer_http_url_port() {
    local url="$1"
    local explicit_port scheme
    explicit_port="$(extract_http_url_port "$url")"
    if [[ -n "$explicit_port" ]]; then
        printf '%s' "$explicit_port"
        return 0
    fi

    scheme="$(extract_http_url_scheme "$url")"
    if [[ "$scheme" == "https" ]]; then
        printf '443'
    else
        printf '80'
    fi
}

resolve_admin_backend_base_url() {
    local svc_dir="$1"
    local backend_host="$2"
    local env_file="$svc_dir/.env"
    local current_url scheme port derived_url prompt_url

    current_url="$(get_env_value "$env_file" "ADMIN_BACKEND_BASE_URL")"
    current_url="${current_url%/}"
    scheme="$(extract_http_url_scheme "$current_url")"
    port="$(extract_http_url_port "$current_url")"
    scheme="${scheme:-https}"
    port="${port:-8443}"
    derived_url="${scheme}://${backend_host}:${port}"

    if [[ -n "$current_url" ]]; then
        if [[ "$current_url" =~ ^https?://${backend_host}(:[0-9]+)?/?$ ]]; then
            printf '%s' "$current_url"
            return 0
        fi

        if [[ -t 0 ]]; then
            info "[microservice_admin] Текущий ADMIN_BACKEND_BASE_URL: $current_url" >&2
            read -rp "[microservice_admin] Введите ADMIN_BACKEND_BASE_URL [$derived_url]: " prompt_url
            prompt_url="${prompt_url:-$derived_url}"
        else
            prompt_url="$derived_url"
        fi
    else
        if [[ -t 0 ]]; then
            info "[microservice_admin] ADMIN_BACKEND_BASE_URL не задан — настроим split HTTPS endpoint." >&2
            read -rp "[microservice_admin] Введите ADMIN_BACKEND_BASE_URL [$derived_url]: " prompt_url
            prompt_url="${prompt_url:-$derived_url}"
        else
            prompt_url="$derived_url"
        fi
    fi

    prompt_url="$(printf '%s' "$prompt_url" | tr -d '\r' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    prompt_url="${prompt_url%/}"
    validate_backend_base_url "$prompt_url"
    printf '%s' "$prompt_url"
}

resolve_backend_public_base_url() {
    local env_file="$1"
    local current_url prompt_url=""

    current_url="$(get_env_value "$env_file" "PUBLIC_DOWNLOAD_BASE_URL")"
    current_url="${current_url%/}"

    if [[ -n "$current_url" && "$current_url" != "http://localhost:8501" ]]; then
        validate_backend_base_url "$current_url"
        printf '%s' "$current_url"
        return 0
    fi

    [[ -t 0 ]] || fail "[backend-host] PUBLIC_DOWNLOAD_BASE_URL не задан. Запусти интерактивно или заранее заполни $env_file"

    info "[backend-host] Нужен внешний base URL backend-host для HTTPS admin facade и прямых downloads." >&2
    while [[ -z "$prompt_url" ]]; do
        read -rp "[backend-host] Введите backend public base URL (например https://backend.example.com:8443): " prompt_url
        prompt_url="$(printf '%s' "$prompt_url" | tr -d '\r' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
        prompt_url="${prompt_url%/}"
        [[ -z "$prompt_url" ]] && warn "[backend-host] Base URL не может быть пустым." >&2
    done

    validate_backend_base_url "$prompt_url"
    printf '%s' "$prompt_url"
}

configure_backend_http_facade_env() {
    local infra_svc_dir data_svc_dir infra_env data_env
    local public_base_url backend_port

    infra_svc_dir="$(get_service_directory "microservice_infra")"
    data_svc_dir="$(get_service_directory "microservice_data")"

    infra_env="$(ensure_env_file "$infra_svc_dir")"
    data_env="$(ensure_env_file "$data_svc_dir")"

    public_base_url="$(resolve_backend_public_base_url "$data_env")"
    backend_port="$(infer_http_url_port "$public_base_url")"

    set_env_value "$data_env" "PUBLIC_DOWNLOAD_BASE_URL" "$public_base_url"
    set_env_value "$infra_env" "ADMIN_BACKEND_PORT" "$backend_port"

    success "[backend-host] HTTP admin facade env настроены: PUBLIC_DOWNLOAD_BASE_URL=$public_base_url, ADMIN_BACKEND_PORT=$backend_port."
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

ensure_admin_online_public_env_defaults() {
    local env_file="$1"

    [[ -n "$(get_env_value "$env_file" "ADMIN_HTTP_PORT")" ]] || set_env_value "$env_file" "ADMIN_HTTP_PORT" "80"
    [[ -n "$(get_env_value "$env_file" "ADMIN_HTTPS_PORT")" ]] || set_env_value "$env_file" "ADMIN_HTTPS_PORT" "443"
    [[ -n "$(get_env_value "$env_file" "ADMIN_PRIMARY_DOMAIN")" ]] || set_env_value "$env_file" "ADMIN_PRIMARY_DOMAIN" "sha-trade.tech"
    [[ -n "$(get_env_value "$env_file" "ADMIN_SECONDARY_DOMAIN")" ]] || set_env_value "$env_file" "ADMIN_SECONDARY_DOMAIN" "www.sha-trade.tech"
    [[ -n "$(get_env_value "$env_file" "ADMIN_TLS_CERT_PATH")" ]] || set_env_value "$env_file" "ADMIN_TLS_CERT_PATH" "/etc/letsencrypt/live/sha-trade.tech/fullchain.pem"
    [[ -n "$(get_env_value "$env_file" "ADMIN_TLS_KEY_PATH")" ]] || set_env_value "$env_file" "ADMIN_TLS_KEY_PATH" "/etc/letsencrypt/live/sha-trade.tech/privkey.pem"
}

sync_admin_online_direct_runtime_env() {
    local env_file="$1"
    local backend_host="$2"

    set_env_value "$env_file" "KAFKA_BOOTSTRAP_SERVERS" "$backend_host:9092"
    set_env_value "$env_file" "REDPANDA_ADMIN_URL" "$backend_host:9644"
    set_env_value "$env_file" "ACCOUNT_URL" "$backend_host:7510"
    set_env_value "$env_file" "GATEWAY_URL" "$backend_host:7520"
    set_env_value "$env_file" "MINIO_URL" "$backend_host:9000"
    set_env_value "$env_file" "BACKEND_CONNECTION_TARGET" "$backend_host"
}

print_admin_online_env_summary() {
    local env_file="$1"
    local summary_keys=(
        ONLINE_BACKEND_HOST
        ONLINE_KAFKA_BOOTSTRAP_SERVERS
        ONLINE_REDPANDA_ADMIN_URL
        ONLINE_ACCOUNT_URL
        ONLINE_GATEWAY_URL
        ONLINE_MINIO_URL
        ACCOUNT_URL
        GATEWAY_URL
        REDPANDA_ADMIN_URL
        MINIO_URL
        KAFKA_BOOTSTRAP_SERVERS
        BACKEND_CONNECTION_TARGET
        ADMIN_BACKEND_BASE_URL
        ADMIN_BACKEND_TLS_INSECURE
        ADMIN_HTTP_PORT
        ADMIN_HTTPS_PORT
        ADMIN_PRIMARY_DOMAIN
        ADMIN_SECONDARY_DOMAIN
    )

    info "[microservice_admin] Итоговая admin-конфигурация:"
    local key value
    for key in "${summary_keys[@]}"; do
        value="$(get_env_value "$env_file" "$key")"
        info "[microservice_admin]   $key=$value"
    done
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

    local backend_host admin_backend_base_url
    backend_host="$(resolve_admin_online_backend_host "$svc_dir" "$explicit_backend_host")"
    admin_backend_base_url="$(resolve_admin_backend_base_url "$svc_dir" "$backend_host")"

    set_env_value "$env_file" "ONLINE_BACKEND_HOST" "$backend_host"
    set_env_value "$env_file" "ONLINE_KAFKA_BOOTSTRAP_SERVERS" "$backend_host:9092"
    set_env_value "$env_file" "ONLINE_REDPANDA_ADMIN_URL" "$backend_host:9644"
    set_env_value "$env_file" "ONLINE_ACCOUNT_URL" "$backend_host:7510"
    set_env_value "$env_file" "ONLINE_GATEWAY_URL" "$backend_host:7520"
    set_env_value "$env_file" "ONLINE_MINIO_URL" "$backend_host:9000"
    set_env_value "$env_file" "ADMIN_BACKEND_BASE_URL" "$admin_backend_base_url"
    sync_admin_online_direct_runtime_env "$env_file" "$backend_host"
    ensure_admin_online_public_env_defaults "$env_file"
    if [[ -z "$(get_env_value "$env_file" "ADMIN_BACKEND_TLS_INSECURE")" && "$admin_backend_base_url" == https://* ]]; then
        set_env_value "$env_file" "ADMIN_BACKEND_TLS_INSECURE" "1"
    fi

    success "[microservice_admin] Split env настроены: ONLINE_* + ADMIN_BACKEND_BASE_URL для $admin_backend_base_url"
    print_admin_online_env_summary "$env_file"
}

# ── Очистка dangling-образов ──────────────────────────────────────────────────
remove_dangling_images() {
    if [[ "${MODELLINE_SKIP_DOCKER_PRUNE:-0}" == "1" ]]; then
        return 0
    fi

    if docker images -f "dangling=true" -q | grep -q .; then
        local prune_output=""
        local prune_status=0
        local prune_lock_dir="$REPO_ROOT/.runtime-data/.docker-image-prune.lock"

        mkdir -p "$REPO_ROOT/.runtime-data"
        if ! mkdir "$prune_lock_dir" 2>/dev/null; then
            warn "Пропускаем docker image prune: cleanup уже выполняется другим launcher-процессом."
            return 0
        fi

        info "Удаляем dangling-образы Docker..."
        prune_output="$(docker image prune -f 2>&1)" || prune_status=$?
        rmdir "$prune_lock_dir" 2>/dev/null || true

        if [[ $prune_status -ne 0 ]]; then
            if grep -qi "prune operation is already running" <<< "$prune_output"; then
                warn "Пропускаем docker image prune: уже выполняется другая операция prune."
            elif [[ -n "$prune_output" ]]; then
                warn "Не удалось выполнить docker image prune: $prune_output"
            else
                warn "Не удалось выполнить docker image prune."
            fi
        fi
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
        MODELLINE_SKIP_DOCKER_PRUNE=1 bash "$SCRIPT_DIR/start.sh" "$svc" "$mode" &
        pids+=("$!")
        names+=("$svc")
    done

    local failed=0
    local -a failed_names=()
    local idx
    for idx in "${!pids[@]}"; do
        if wait "${pids[$idx]}"; then
            success "[${names[$idx]}] Параллельный запуск завершён."
        else
            warn "[${names[$idx]}] Параллельный запуск завершился с ошибкой."
            failed=1
            failed_names+=("${names[$idx]}")
        fi
    done

    remove_dangling_images

    [[ $failed -eq 0 ]] || fail "Параллельный запуск завершился с ошибкой для: ${failed_names[*]}"
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
    preflight_check_host_ports "$name" "$mode" "$svc_dir"

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
            docker compose --profile online up -d admin-online admin-online-proxy
            ;;
        core|"")
            docker compose up -d
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

    BACKEND_HOST="$(resolve_admin_online_backend_host "$admin_svc_dir" "${BACKEND_HOST:-}")"

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
                configure_backend_http_facade_env
            fi
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