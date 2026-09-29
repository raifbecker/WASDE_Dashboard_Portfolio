#!/usr/bin/env bash
# Load NASS Grain Stocks data (released quarterly: end of Mar, Jun, Sep, Dec).
# Run on 1st of Jan, Apr, Jul, Oct.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_in_docker.sh" --grain-stocks
