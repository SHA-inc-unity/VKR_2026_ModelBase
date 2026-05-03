#!/usr/bin/env bash
# =============================================================================
# microservicestarter — restart.sh
#
# Обновляет код через git pull и перезапускает один или все микросервисы.
#
# Использование:
#   ./restart.sh                               — git pull + перезапустить все
#   ./restart.sh all noadmin                  — git pull + всё, кроме admin
#   ./restart.sh all onlyadmin                — git pull + только admin-head
#   ./restart.sh microservice_analitic         — git pull + перезапустить сервис
#   ./restart.sh all                           — git pull + перезапустить все
#   ./restart.sh microservice_admin onlyadmin  — git pull + только admin-head
#   ./restart.sh microservice_analitic full    — core + scheduler
#   ./restart.sh microservice_analitic api     — только api-контейнер
#   ./restart.sh microservice_analitic deps    — пересобрать base + перезапустить
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
command -v git >/dev/null 2>&1          || warn "git не найден — шаг git pull будет пропущен."

declare -A SERVICE_PATHS
declare -a SERVICE_ORDER
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    read -r svc_name svc_path <<< "$line"
    SERVICE_PATHS["$svc_name"]="$svc_path"
    SERVICE_ORDER+=("$svc_name")
done < "$CONF"

get_service_directory() {
    local name="$1"
    local path="${SERVICE_PATHS[$name]:-}"
    [[ -n "$path" ]] || fail "Сервис '$name' не найден в services.conf"
    local svc_dir="$REPO_ROOT/$path"
    [[ -d "$svc_dir" ]] || fail "Директория не найдена: $svc_dir"
    echo "$svc_dir"
}

# ── git pull (выполняется один раз на весь репозиторий) ────────────────────
git_pull_done=0
do_git_pull() {
    if [[ "$git_pull_done" -eq 0 ]]; then
        if [[ "${MODELLINE_SKIP_GIT_PULL:-0}" == "1" ]]; then
            git_pull_done=1
            return
        fi
        info "git pull — загружаем последние изменения..."
        pushd "$REPO_ROOT" > /dev/null
        if command -v git >/dev/null 2>&1; then
            git pull && success "git pull завершён." || warn "git pull завершился с ошибкой — продолжаем с локальным кодом."
        else
            warn "git не найден — пропускаем git pull."
        fi
        popd > /dev/null
        git_pull_done=1
    fi
}

run_parallel_restart_selection() {
    local mode="$1"
    shift
    local services=("$@")
    [[ ${#services[@]} -gt 0 ]] || return 0

    info "Параллельный перезапуск: ${services[*]}"
    local -a pids=()
    local -a names=()
    local svc
    for svc in "${services[@]}"; do
        MODELLINE_SKIP_GIT_PULL=1 "$SCRIPT_DIR/restart.sh" "$svc" "$mode" &
        pids+=("$!")
        names+=("$svc")
    done

    local failed=0
    local idx
    for idx in "${!pids[@]}"; do
        if wait "${pids[$idx]}"; then
            success "[${names[$idx]}] Параллельный перезапуск завершён."
        else
            warn "[${names[$idx]}] Параллельный перезапуск завершился с ошибкой."
            failed=1
        fi
    done

    [[ $failed -eq 0 ]] || fail "Параллельный перезапуск завершился с ошибкой."
}

restart_service() {
    local name="$1"
    local mode="${2:-core}"
    local svc_dir
    svc_dir="$(get_service_directory "$name")"

    info "[$name] Перезапуск (mode=$mode)..."
    pushd "$svc_dir" > /dev/null

    local base_tag="${name}-base:latest"
    local compose_content
    compose_content=$(cat "$svc_dir/docker-compose.yml" 2>/dev/null || true)
    local has_base=0 has_api=0
    echo "$compose_content" | grep -qE '^  base\s*:'      && has_base=1      || true
    echo "$compose_content" | grep -qE '^  api\s*:'       && has_api=1       || true

    local base_found=0
    if [[ $has_base -eq 1 ]] && docker image inspect "$base_tag" >/dev/null 2>&1; then
        base_found=1
    fi

    case "$mode" in
        onlyadmin)
            [[ "$name" == "microservice_admin" ]] || fail "[$name] mode=onlyadmin поддерживается только для microservice_admin"
            docker compose --profile online up -d --build admin-online
            ;;
        deps)
            if [[ $has_base -eq 1 ]]; then
                info "[$name] Пересборка base-образа (requirements.txt изменился)..."
                docker compose --profile build-base build --no-cache base
                docker images -f "dangling=true" -q | grep -q . && docker image prune -f >/dev/null
            fi
            docker compose build
            docker images -f "dangling=true" -q | grep -q . && docker image prune -f >/dev/null
            docker compose up -d
            ;;
        api)
            if [[ $has_api -eq 1 ]]; then
                docker compose build api
                docker images -f "dangling=true" -q | grep -q . && docker image prune -f >/dev/null
                docker compose up -d --no-deps api
            else
                docker compose build
                docker images -f "dangling=true" -q | grep -q . && docker image prune -f >/dev/null
                docker compose up -d
            fi
            success "[$name] api перезапущен."
            ;;

        full)
            if [[ $has_base -eq 1 && $base_found -eq 0 ]]; then
                docker compose --profile build-base build base
                docker images -f "dangling=true" -q | grep -q . && docker image prune -f >/dev/null
            fi
            if [[ $has_api -eq 1 ]]; then
                docker compose build api
            else
                docker compose build
            fi
            docker images -f "dangling=true" -q | grep -q . && docker image prune -f >/dev/null
            docker compose --profile scheduler up -d
            ;;
        core|"")
            if [[ $has_base -eq 1 && $base_found -eq 0 ]]; then
                info "[$name] Сборка base-образа..."
                docker compose --profile build-base build base
                docker images -f "dangling=true" -q | grep -q . && docker image prune -f >/dev/null
            fi
            if [[ $has_api -eq 1 ]]; then
                docker compose build api
            else
                docker compose build
            fi
            docker images -f "dangling=true" -q | grep -q . && docker image prune -f >/dev/null
            docker compose up -d
            ;;
        *)
            fail "[$name] Неизвестный режим: $mode"
            ;;
    esac

    popd > /dev/null
    success "[$name] Перезапущен."
}

TARGET="${1:-all}"
MODE="${2:-core}"

do_git_pull

if [[ "$MODE" == "onlyadmin" ]]; then
    [[ "$TARGET" == "all" || "$TARGET" == "microservice_admin" ]] || fail "mode=onlyadmin поддерживается только для microservice_admin"
    restart_service "microservice_admin" "onlyadmin"
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

    if [[ ${#selected_services[@]} -gt 1 ]]; then
        if [[ " ${selected_services[*]} " == *" microservice_infra "* ]]; then
            restart_service "microservice_infra" "$dispatch_mode"
            filtered_services=()
            for svc in "${selected_services[@]}"; do
                [[ "$svc" == "microservice_infra" ]] || filtered_services+=("$svc")
            done
            selected_services=("${filtered_services[@]}")
        fi

        if [[ ${#selected_services[@]} -gt 1 ]]; then
            run_parallel_restart_selection "$dispatch_mode" "${selected_services[@]}"
        elif [[ ${#selected_services[@]} -eq 1 ]]; then
            restart_service "${selected_services[0]}" "$dispatch_mode"
        fi
    else
        for svc in "${selected_services[@]}"; do
            restart_service "$svc" "$dispatch_mode"
        done
    fi
fi