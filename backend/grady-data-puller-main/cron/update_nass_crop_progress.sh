#!/usr/bin/env bash
# Load NASS Crop Progress data (released weekly Mon during growing season Apr-Nov).
# Run every Tuesday during April through November.
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

source .venv/bin/activate

python3 -m src.main --crop-progress
