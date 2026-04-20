#!/usr/bin/env bash
# =============================================================================
# microservicestarter — update.sh
#
# Только git pull — без перезапуска контейнеров.
# Используйте restart.sh если нужен полный цикл обновления.
#
# Использование:
#   ./update.sh   — git pull в корне репозитория
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${CYAN}[starter]${NC} $*"; }
success() { echo -e "${GREEN}[starter]${NC} $*"; }
warn()    { echo -e "${YELLOW}[starter]${NC} $*"; }
fail()    { echo -e "${RED}[starter] ERROR:${NC} $*"; exit 1; }

command -v git >/dev/null 2>&1 || fail "git не найден."

info "git pull — загружаем последние изменения из репозитория..."
cd "$REPO_ROOT"
git pull && success "Репозиторий обновлён." || warn "git pull завершился с ошибкой."
info "Для применения изменений запустите: ./restart.sh [service|all]"