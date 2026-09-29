#!/usr/bin/env bash
# Promote rough WASDE data to final after the official CSV is published.
# Run a few days after the rough load (around 14th-16th of each month).
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

source .venv/bin/activate

python3 -m src.main --promote-rough
