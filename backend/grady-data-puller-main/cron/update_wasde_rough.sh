#!/usr/bin/env bash
# Load the rough/preliminary WASDE TXT report for the current month.
# Run on WASDE report day (typically 9th-12th of each month).
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

source .venv/bin/activate

python3 -m src.main --rough
