#!/usr/bin/env bash
# set_token.sh — set the admin-backend token on the admin host.
#
# Default target:
#   ../microservice_admin/.env ← ADMIN_BACKEND_SHARED_TOKEN
#
# Usage:
#   ./set_token.sh <big-token>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${MODELLINE_ADMIN_ENV_FILE:-$REPO_ROOT/microservice_admin/.env}"
ENV_EXAMPLE="${MODELLINE_ADMIN_ENV_EXAMPLE:-$REPO_ROOT/microservice_admin/.env.example}"
KEY="ADMIN_BACKEND_SHARED_TOKEN"

usage() {
  cat <<'EOF'
Usage:
  ./set_token.sh <big-token>

Writes ADMIN_BACKEND_SHARED_TOKEN into ../microservice_admin/.env.
EOF
}

if [[ $# -eq 1 && ( "$1" == "-h" || "$1" == "--help" ) ]]; then
  usage
  exit 0
fi

if [[ $# -ne 1 ]]; then
  echo "set_token.sh expects exactly one positional token argument." >&2
  exit 1
fi


TOKEN="$(printf '%s' "$1" | tr -d '\r\n')"

if [[ -z "$TOKEN" ]]; then
  echo "Token argument cannot be empty." >&2
  exit 1
fi
TARGET_DIR="$(dirname "$ENV_FILE")"
mkdir -p "$TARGET_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$ENV_EXAMPLE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
  else
    : > "$ENV_FILE"
  fi
fi

if grep -q -E "^${KEY}=" "$ENV_FILE" 2>/dev/null; then
  sed -i.bak -E "s|^${KEY}=.*$|${KEY}=${TOKEN}|" "$ENV_FILE"
  rm -f "$ENV_FILE.bak"
else
  printf '\n%s=%s\n' "$KEY" "$TOKEN" >> "$ENV_FILE"
fi

echo "Updated $ENV_FILE with $KEY" >&2