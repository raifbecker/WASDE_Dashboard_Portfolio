#!/usr/bin/env bash
# Load NASS Acreage report data (released end of June).
# Run once in early July.
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

source .venv/bin/activate

python3 -m src.main --acreage
