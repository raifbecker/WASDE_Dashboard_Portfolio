#!/usr/bin/env bash
# Load NASS Prospective Plantings data (released end of March).
# Run once in early April.
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

source .venv/bin/activate

python3 -m src.main --nass
