#!/usr/bin/env bash
# Load NASS Acreage report data (released end of June).
# Run once in early July.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_in_docker.sh" --acreage
