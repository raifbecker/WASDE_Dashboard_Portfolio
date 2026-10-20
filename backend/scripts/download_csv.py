#!/usr/bin/env python3
"""Download validated WASDE CSVs. --refresh also checks existing remote files."""
import argparse
import io
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.csv_loader import _validate

BASE_URL = 'https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-{year}-{month:02d}.csv'
HEADERS = {'User-Agent': 'WASDE-Portfolio/1.0'}
ARCHIVE_URL = 'https://www.usda.gov/historical-wasde-report-data-3'
# Verified against USDA's archive on 2026-10-20. The archive HTML can return
# HTTP 403 even while CSV downloads work; retain these known published links.
VERIFIED_REPORT_URLS = {
    month: f'https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-{month}-V2.csv'
    for month in ('2026-05', '2026-06')
}
logger = logging.getLogger(__name__)


class ReportLinks(HTMLParser):
    """Use USDA's actual links, including corrected filenames such as -V2.csv."""
    def __init__(self):
        super().__init__()
        self.urls = {}

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        href = dict(attrs).get('href', '')
        url = urljoin(ARCHIVE_URL, href)
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in {'www.usda.gov', 'usda.gov'}:
            return
        match = re.search(r'/oce-wasde-report-data-(\d{4}-\d{2})(?:[-_][^/]*)?\.csv$', parsed.path, re.I)
        if match:
            self.urls[match[1]] = url


def discover_report_urls():
    try:
        response = requests.get(ARCHIVE_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        parser = ReportLinks()
        parser.feed(response.content.decode('utf-8'))
        if not parser.urls:
            raise ValueError('No monthly CSV links found')
        return {**VERIFIED_REPORT_URLS, **parser.urls}
    except (requests.RequestException, ValueError, UnicodeError) as error:
        print(f'Warning: archive discovery failed; using verified versioned links and standard filenames: {error}')
        return dict(VERIFIED_REPORT_URLS)


def download_file(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return (response.content, response.status_code) if response.content else (None, 'empty')
    except requests.exceptions.HTTPError as error:
        return None, f'HTTP {error.response.status_code}'
    except requests.exceptions.Timeout:
        return None, 'timeout'
    except requests.exceptions.RequestException as error:
        return None, type(error).__name__


def save_csv(content, path, report_month):
    """Validate before replacing a cached file; os.replace is atomic on one disk."""
    df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
    records = _validate(df)
    if any(not record['ReportDate'].startswith(report_month) for record in records):
        raise ValueError('Downloaded report month does not match requested month')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.part', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data_dir', type=Path)
    parser.add_argument('start_year', type=int)
    parser.add_argument('end_year', type=int)
    parser.add_argument('end_month', type=int)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    if args.start_year > args.end_year or not 1 <= args.end_month <= 12:
        parser.error('Invalid date range')
    args.data_dir.mkdir(parents=True, exist_ok=True)
    needed = any(args.refresh or not (args.data_dir / f'oce-wasde-report-data-{year}-{month:02d}.csv').exists()
                 for year in range(args.start_year, args.end_year + 1)
                 for month in range(1, (args.end_month if year == args.end_year else 12) + 1))
    report_urls = discover_report_urls() if needed else {}
    failures = 0
    for year in range(args.start_year, args.end_year + 1):
        for month in range(1, (args.end_month if year == args.end_year else 12) + 1):
            path = args.data_dir / f'oce-wasde-report-data-{year}-{month:02d}.csv'
            if path.exists() and not args.refresh:
                print(f'Already exists: {path}')
                continue
            url = report_urls.get(f'{year}-{month:02d}', BASE_URL.format(year=year, month=month))
            print(f'Downloading {url} ...')
            content, status = download_file(url)
            if content is None:
                print(f'Unavailable ({status}); existing file retained: {path}')
                failures += status != 'HTTP 404'
                continue
            try:
                save_csv(content, path, f'{year}-{month:02d}')
            except (ValueError, UnicodeError, pd.errors.ParserError) as error:
                print(f'Rejected download; existing file retained: {error}')
                failures += 1
                continue
            print(f'Saved validated CSV: {path}')
            time.sleep(1)
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
