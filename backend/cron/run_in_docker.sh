#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

mkdir -p "$PROJECT_DIR/data"

exec docker compose \
    --project-directory "$PROJECT_DIR" \
    -f "$PROJECT_DIR/compose.yaml" \
    run --rm --build --no-deps loader \
    --db /app/data/wasde.db "$@"
