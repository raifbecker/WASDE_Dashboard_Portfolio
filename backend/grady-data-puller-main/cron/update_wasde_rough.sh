#!/usr/bin/env bash
# Load the rough/preliminary WASDE TXT report for the current month.
# Run on WASDE report day (typically 9th-12th of each month).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_in_docker.sh" --rough
