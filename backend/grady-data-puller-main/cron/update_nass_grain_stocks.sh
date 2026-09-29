#!/usr/bin/env bash
# Load NASS Grain Stocks data (released quarterly: end of Mar, Jun, Sep, Dec).
# Run on 1st of Jan, Apr, Jul, Oct.
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

source .venv/bin/activate

python3 -m src.main --grain-stocks
