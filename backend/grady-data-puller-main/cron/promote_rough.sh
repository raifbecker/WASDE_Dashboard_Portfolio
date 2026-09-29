#!/usr/bin/env bash
# Promote rough WASDE data to final after the official CSV is published.
# Run a few days after the rough load (around 14th-16th of each month).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_in_docker.sh" --promote-rough
