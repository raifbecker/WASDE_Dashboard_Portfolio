#!/usr/bin/env bash
set -e

DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/data/csv"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DATA_DIR"

# Determine date range: START_YEAR-01 through current year/month
CURRENT_YEAR=$(date +%Y)
CURRENT_MONTH=$(date +%m)

START_YEAR=2021

echo "Downloading WASDE CSVs from ${START_YEAR}-01 through ${CURRENT_YEAR}-${CURRENT_MONTH}..."

# Use Python for downloads — curl hangs against USDA's server
python3 "$SCRIPT_DIR/download_csv.py" "$DATA_DIR" "$START_YEAR" "$CURRENT_YEAR" "$CURRENT_MONTH"

echo "Done. CSV files in $DATA_DIR"
