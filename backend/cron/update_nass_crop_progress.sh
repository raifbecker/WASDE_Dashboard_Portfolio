#!/usr/bin/env bash
# Load NASS Crop Progress data (released weekly Mon during growing season Apr-Nov).
# Run every Tuesday during April through November.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_in_docker.sh" --crop-progress
