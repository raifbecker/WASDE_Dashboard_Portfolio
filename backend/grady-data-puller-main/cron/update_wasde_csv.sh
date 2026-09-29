#!/usr/bin/env bash
# Download the latest WASDE CSV and load it into the database.
# Run daily from the 8th-15th of each month to catch publication day.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_in_docker.sh" --download --load-csv
