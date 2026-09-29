#!/usr/bin/env bash
# Load NASS Prospective Plantings data (released end of March).
# Run once in early April.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_in_docker.sh" --nass
