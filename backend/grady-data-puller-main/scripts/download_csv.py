#!/usr/bin/env python3
"""Download missing WASDE CSV files from USDA.

Usage: python3 download_csv.py <data_dir> <start_year> <end_year> <end_month>

Uses Python requests instead of curl because USDA's server hangs
on curl's HTTP/1.1 keep-alive negotiation.
"""

import os
import sys
import time

import requests

BASE_URL = "https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-{year}-{month:02d}.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:110.0) Gecko/20100101 Firefox/110.0"}


def download_file(url):
    """Download with a short timeout. USDA hangs on missing files instead of returning 404."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        if len(resp.content) == 0:
            return None, "empty"
        return resp.content, resp.status_code
    except requests.exceptions.HTTPError as e:
        return None, f"HTTP {e.response.status_code}"
    except requests.exceptions.RequestException as e:
        return None, "timeout"


def main():
    if len(sys.argv) != 5:
        print(f"Usage: {sys.argv[0]} <data_dir> <start_year> <end_year> <end_month>")
        sys.exit(1)

    data_dir = sys.argv[1]
    start_year = int(sys.argv[2])
    end_year = int(sys.argv[3])
    end_month = int(sys.argv[4])

    os.makedirs(data_dir, exist_ok=True)

    for year in range(start_year, end_year + 1):
        last_month = end_month if year == end_year else 12
        for month in range(1, last_month + 1):
            filename = f"oce-wasde-report-data-{year}-{month:02d}.csv"
            filepath = os.path.join(data_dir, filename)

            if os.path.exists(filepath):
                print(f"Already exists: {filepath}")
                continue

            url = BASE_URL.format(year=year, month=month)
            print(f"Downloading {url} ...")

            content, status = download_file(url)
            if content is None:
                print(f"  -> not available ({status}), skipping")
                continue

            with open(filepath, "wb") as f:
                f.write(content)
            lines = content.count(b"\n")
            print(f"  -> saved {filepath} ({lines} lines)")

            time.sleep(1)


if __name__ == "__main__":
    main()
