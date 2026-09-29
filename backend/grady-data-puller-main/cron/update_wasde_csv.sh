#!/usr/bin/env bash
# Download the latest WASDE CSV and load it into the database.
# Run daily from the 8th-15th of each month to catch publication day.
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

source .venv/bin/activate

python3 -m src.main --download --load-csv
