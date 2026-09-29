#!/usr/bin/env bash
set -e

# Downloads the latest WASDE CSV for the current month (or a given YYYY-MM).
# Usage:
#   ./scripts/download_latest_csv.sh            # current year/month
#   ./scripts/download_latest_csv.sh 2026-04    # specific month

DATA_DIR="$(dirname "$0")/../data/csv"
mkdir -p "$DATA_DIR"

if [ -n "$1" ]; then
    YEAR="${1%%-*}"
    MONTH="${1##*-}"
else
    YEAR="$(date +%Y)"
    MONTH="$(date +%m)"
fi

FILE="$DATA_DIR/oce-wasde-report-data-${YEAR}-${MONTH}.csv"
URL="https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-${YEAR}-${MONTH}.csv"

if [ -f "$FILE" ]; then
    echo "Already exists: $FILE"
    exit 0
fi

echo "Downloading $URL ..."
HTTP_CODE=$(curl -L --retry 3 --retry-delay 3 --retry-all-errors --max-time 60 --http1.1 -o "$FILE" -w "%{http_code}" "$URL" 2>/dev/null)

if [ "$HTTP_CODE" -ge 400 ] 2>/dev/null || [ ! -s "$FILE" ]; then
    rm -f "$FILE"
    echo "Failed to download (HTTP $HTTP_CODE). The report for ${YEAR}-${MONTH} may not be published yet."
    exit 1
fi

echo "Saved $FILE ($(wc -l < "$FILE") lines)"
