#!/usr/bin/env bash
# print_token.sh — print the current backend admin token.
#
# Default source:
#   ../microservice_gateway/.env → ADMIN_SHARED_TOKEN
#
# Usage:
#   ./print_token.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${MODELLINE_TOKEN_ENV_FILE:-$REPO_ROOT/microservice_gateway/.env}"
KEY="${MODELLINE_TOKEN_KEY:-ADMIN_SHARED_TOKEN}"

usage() {
  cat <<'EOF'
Usage:
  ./print_token.sh

Prints ADMIN_SHARED_TOKEN from ../microservice_gateway/.env.
EOF
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "print_token.sh does not accept positional arguments." >&2
      usage >&2
      exit 1
      ;;
  esac
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

VALUE="$(sed -n "s/^${KEY}=//p" "$ENV_FILE" | head -n1 | tr -d '\r')"

if [[ -z "$VALUE" ]]; then
  echo "Key '$KEY' not found or empty in $ENV_FILE" >&2
  exit 1
fi

printf '%s\n' "$VALUE"