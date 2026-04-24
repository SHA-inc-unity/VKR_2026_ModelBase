#!/usr/bin/env bash
# =============================================================================
# microservicestarter — restart.sh
#
# Обновляет код через git pull и перезапускает один или все микросервисы.
#
# Использование:
#   ./restart.sh                               — git pull + перезапустить все
#   ./restart.sh microservice_analitic         — git pull + перезапустить сервис
#   ./restart.sh all                           — git pull + перезапустить все
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
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    read -r svc_name svc_path <<< "$line"
    SERVICE_PATHS["$svc_name"]="$svc_path"
done < "$CONF"

# ── git pull (выполняется один раз на весь репозиторий) ────────────────────
git_pull_done=0
do_git_pull() {
    if [[ "$git_pull_done" -eq 0 ]]; then
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

restart_service() {
    local name="$1"
    local mode="${2:-core}"
    local path="${SERVICE_PATHS[$name]:-}"
    [[ -z "$path" ]] && fail "Сервис '$name' не найден в services.conf"
    local svc_dir="$REPO_ROOT/$path"
    [[ -d "$svc_dir" ]] || fail "Директория не найдена: $svc_dir"

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

if [[ "$TARGET" == "all" ]]; then
    for svc in "${!SERVICE_PATHS[@]}"; do restart_service "$svc" "$MODE"; done
else
    restart_service "$TARGET" "$MODE"
fi