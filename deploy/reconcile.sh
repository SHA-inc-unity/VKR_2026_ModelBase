#!/usr/bin/env bash
# reconcile.sh — ModelLine deployment reconcile (Linux / macOS / WSL)
#
# Usage:
#   ./reconcile.sh [--service <name>] [--dry-run] [--config <path>]
#
# Reads deploy/modelline-deploy.yml and for each service entry:
#   1. Pulls the latest image (if pull: true).
#   2. Restarts (docker compose up -d) based on restart_policy:
#      "if_changed" — only when the image digest changed (default)
#      "always"     — unconditionally
#      "never"      — pull only, no restart

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/modelline-deploy.yml"
FILTER_SERVICE=""
DRY_RUN=false

# ── Args ──────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --service)  FILTER_SERVICE="$2"; shift 2 ;;
    --config)   CONFIG_FILE="$2";    shift 2 ;;
    --dry-run)  DRY_RUN=true;        shift   ;;
    *) echo "Unknown argument: $1"; exit 1   ;;
  esac
done

# ── Colours ───────────────────────────────────────────────────────────────────
C_CYAN='\033[0;36m'
C_GREEN='\033[0;32m'
C_YELLOW='\033[1;33m'
C_MAGENTA='\033[0;35m'
C_GRAY='\033[0;37m'
C_RESET='\033[0m'

step()  { echo -e "  ${C_CYAN}>> $*${C_RESET}"; }
ok()    { echo -e "  ${C_GREEN}OK $*${C_RESET}"; }
skip()  { echo -e "  ${C_GRAY}-- $*${C_RESET}"; }
warn()  { echo -e "  ${C_YELLOW}!! $*${C_RESET}"; }

run_docker() {
  if $DRY_RUN; then
    echo -e "  ${C_YELLOW}[DRY-RUN] docker $*${C_RESET}"
    return 0
  fi
  docker "$@"
}

# ── Check config ──────────────────────────────────────────────────────────────
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Config not found: $CONFIG_FILE"
  exit 1
fi

# ── Minimal YAML parser ───────────────────────────────────────────────────────
# Parses the fixed schema of modelline-deploy.yml without requiring yq/python.

declare -a NAMES COMPOSE_FILES RESTART_POLICIES PULL_FLAGS
declare -a SVC_LISTS

parse_config() {
  local idx=-1
  local in_services=false

  while IFS= read -r raw; do
    line="${raw%$'\r'}"  # strip CR (Windows line endings)

    if [[ "$line" =~ ^[[:space:]]*-[[:space:]]+name:[[:space:]]*(.+) ]]; then
      idx=$((idx + 1))
      NAMES[$idx]="${BASH_REMATCH[1]}"
      COMPOSE_FILES[$idx]=""
      RESTART_POLICIES[$idx]="if_changed"
      PULL_FLAGS[$idx]="true"
      SVC_LISTS[$idx]=""
      in_services=false

    elif [[ $idx -ge 0 && "$line" =~ ^[[:space:]]+compose_file:[[:space:]]*(.+) ]]; then
      COMPOSE_FILES[$idx]="${BASH_REMATCH[1]}"

    elif [[ $idx -ge 0 && "$line" =~ ^[[:space:]]+pull:[[:space:]]*(true|false) ]]; then
      PULL_FLAGS[$idx]="${BASH_REMATCH[1]}"

    elif [[ $idx -ge 0 && "$line" =~ ^[[:space:]]+restart_policy:[[:space:]]*(.+) ]]; then
      RESTART_POLICIES[$idx]="${BASH_REMATCH[1]}"

    elif [[ $idx -ge 0 && "$line" =~ ^[[:space:]]+services:[[:space:]]*$ ]]; then
      in_services=true

    elif $in_services && [[ "$line" =~ ^[[:space:]]+-[[:space:]]+([a-zA-Z0-9_-]+)[[:space:]]*$ ]]; then
      if [[ -n "${SVC_LISTS[$idx]}" ]]; then
        SVC_LISTS[$idx]+=" "
      fi
      SVC_LISTS[$idx]+="${BASH_REMATCH[1]}"

    elif [[ "$line" =~ ^[^[:space:]] && ! "$line" =~ ^[[:space:]]*# ]]; then
      in_services=false
    fi
  done < "$CONFIG_FILE"
}

parse_config

# ── Helpers: digest comparison ────────────────────────────────────────────────

get_running_digest() {
  local compose_path="$1" svc="$2"
  local id
  id=$(docker compose -f "$compose_path" ps -q "$svc" 2>/dev/null || true)
  [[ -z "$id" ]] && { echo ""; return; }
  docker inspect --format '{{.Image}}' "$id" 2>/dev/null || echo ""
}

get_latest_digest() {
  local compose_path="$1" svc="$2"
  local image
  image=$(docker compose -f "$compose_path" config --images 2>/dev/null \
          | grep "$svc" | head -1 | tr -d '[:space:]')
  [[ -z "$image" ]] && { echo ""; return; }
  docker inspect --format '{{index .RepoDigests 0}}' "$image" 2>/dev/null || echo ""
}

# ── Main reconcile loop ───────────────────────────────────────────────────────

TS=$(date '+%Y-%m-%d %H:%M:%S')
echo -e "\n${C_MAGENTA}=== ModelLine Reconcile [$TS]${DRY_RUN:+ (DRY-RUN)} ===${C_RESET}"

for idx in "${!NAMES[@]}"; do
  name="${NAMES[$idx]}"
  raw_path="${COMPOSE_FILES[$idx]}"
  restart="${RESTART_POLICIES[$idx]}"
  pull="${PULL_FLAGS[$idx]}"
  svcs="${SVC_LISTS[$idx]}"

  # Filter by --service arg
  if [[ -n "$FILTER_SERVICE" && "$name" != "$FILTER_SERVICE" ]]; then
    continue
  fi

  # Resolve compose path relative to config dir
  if [[ "$raw_path" = /* ]]; then
    compose_path="$raw_path"
  else
    compose_path="$(cd "$SCRIPT_DIR" && realpath -m "$raw_path")"
  fi

  echo -e "\n${C_MAGENTA}[Service: $name]${C_RESET}"

  if [[ ! -f "$compose_path" ]]; then
    warn "compose file not found: $compose_path — skipping"
    continue
  fi

  for svc in $svcs; do
    step "Processing $svc"

    # 1. Pull
    if [[ "$pull" == "true" ]]; then
      step "Pulling $svc"
      run_docker compose -f "$compose_path" pull "$svc"
    fi

    # 2. Decide restart
    do_restart=false
    case "$restart" in
      always)
        do_restart=true
        step "restart_policy=always → will restart"
        ;;
      never)
        do_restart=false
        skip "restart_policy=never → skip restart"
        ;;
      *)
        # if_changed
        if $DRY_RUN; then
          do_restart=true
        else
          before=$(get_running_digest "$compose_path" "$svc")
          after=$(get_latest_digest   "$compose_path" "$svc")
          if [[ "$before" != "$after" ]]; then
            do_restart=true
            step "Image changed → restarting"
          else
            skip "Image unchanged — no restart needed"
          fi
        fi
        ;;
    esac

    # 3. Restart
    if $do_restart; then
      run_docker compose -f "$compose_path" up -d --no-deps "$svc"
      ok "$svc restarted"
    fi
  done
done

echo -e "\n${C_MAGENTA}=== Reconcile complete ===${C_RESET}\n"
