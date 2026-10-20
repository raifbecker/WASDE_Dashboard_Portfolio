"""Validate a whole CSV, then publish it in one SQLite transaction."""
import hashlib
import io
import logging
import math
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from src.db import get_or_create_region, get_or_create_table_type, get_unit_id

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
CSV_DIR = DATA_DIR / 'csv'
REQUIRED_COLUMNS = {'ReportDate', 'ReportTitle', 'Commodity', 'Region', 'Attribute',
                    'MarketYear', 'Value', 'Unit'}
MISSING = {'', 'nan', 'none', 'null', '(na)', '(x)', '(d)', '(z)', '-', '--'}


def clean(raw):
    if raw is None or pd.isna(raw):
        return ''
    text = str(raw).strip()
    return '' if text.lower() in MISSING else text


def normalize_report_date(raw):
    text = clean(raw)
    if not text:
        return None
    for fmt in ('%B %Y', '%b %Y', '%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    raise ValueError(f'Invalid report date: {text!r}')


def parse_value(raw):
    text = clean(raw).replace(',', '')
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_int(raw):
    value = parse_value(raw)
    return int(value) if value is not None else None


def infer_scope(title):
    if title.startswith(('U.S.', 'US ')):
        return 'U.S.'
    if 'World' in title or 'Foreign' in title:
        return 'World'
    return None


def _validate(df):
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
    if df.empty:
        raise ValueError('CSV contains no observations')
    records = []
    for line, raw in enumerate(df.to_dict('records'), start=2):
        row = {name: clean(value) for name, value in raw.items()}
        for field in REQUIRED_COLUMNS - {'Value', 'MarketYear'}:
            if not row[field]:
                raise ValueError(f'Line {line}: missing {field}')
        if not row['MarketYear']:
            if row.get('ReliabilityProjection') and row['ReportTitle'].startswith('Reliability'):
                row['MarketYear'] = 'Not applicable'
            else:
                raise ValueError(f'Line {line}: missing MarketYear')
        row['ReportDate'] = normalize_report_date(row['ReportDate'])
        row['AnnualQuarterFlag'] = row.get('AnnualQuarterFlag') or 'Annual'
        row['ReliabilityProjection'] = row.get('ReliabilityProjection', '')
        value = parse_value(row['Value'])
        if row['Value'] and value is None:
            raise ValueError(f"Line {line}: invalid numeric value {row['Value']!r}")
        row['Value'] = value
        records.append(row)
    return records


def _load_dataframe(conn, df, source_label, raw_content=None):
    """Return current records published; identical bytes return zero.

    No helper commits. Raw bytes, hash, dimensions, observations and load log
    commit together. An exception restores the last good source snapshot.
    """
    if conn.in_transaction:
        raise ValueError('Commit existing work before loading a CSV')
    content = raw_content if raw_content is not None else df.to_csv(index=False).encode('utf-8')
    digest = hashlib.sha256(content).hexdigest()
    records = _validate(df)
    filename = re.fullmatch(r'oce-wasde-report-data-(\d{4}-\d{2})\.csv', source_label)
    if filename and any(not r['ReportDate'].startswith(filename[1]) for r in records):
        raise ValueError('CSV report month does not match its filename')
    try:
        conn.execute('BEGIN IMMEDIATE')
        previous = conn.execute('SELECT sha256,row_count FROM csv_sources WHERE source_file=?',
                                (source_label,)).fetchone()
        if previous and previous[0] == digest:
            stored = conn.execute('SELECT count(*) FROM wasde_data WHERE csv_source_file=?',
                                  (source_label,)).fetchone()[0]
            if stored == previous[1]:
                conn.rollback()
                logger.info('Skipping %s (unchanged SHA-256)', source_label)
                return 0
        # First replay repairs legacy rows without provenance for this WASDE month.
        # CSV is authoritative over final TXT. NASS rows have no WASDE number.
        if filename and previous is None:
            conn.execute('DELETE FROM wasde_data WHERE csv_source_file IS NULL '
                         'AND wasde_number IS NOT NULL AND substr(report_date,1,7)=?',
                         (filename[1],))
        conn.execute('DELETE FROM wasde_data WHERE csv_source_file=?', (source_label,))
        batch, seen = [], {}
        tables, regions, units = {}, {}, {}
        for row in records:
            title, commodity = row['ReportTitle'], row['Commodity']
            if (title, commodity) not in tables:
                tables[title, commodity] = get_or_create_table_type(
                    conn, title, commodity, infer_scope(title))
            region, unit = row['Region'], row['Unit']
            if region not in regions:
                regions[region] = get_or_create_region(conn, region)
            if unit not in units:
                units[unit] = get_unit_id(conn, unit)
            record = (row['ReportDate'], parse_int(row.get('WasdeNumber')),
                      tables[title, commodity], regions[region], row['Attribute'],
                      row['MarketYear'], row.get('ProjEstFlag') or None,
                      row['AnnualQuarterFlag'], row['Value'], unit, units[unit],
                      row.get('ReleaseDate') or None, row.get('ReleaseTime') or None,
                      parse_int(row.get('ForecastYear')), parse_int(row.get('ForecastMonth')),
                      row['ReliabilityProjection'], source_label)
            key = tuple(record[i] for i in (0, 2, 3, 4, 5, 7, 9, 15))
            if key in seen:
                if seen[key] != record:
                    raise ValueError('Conflicting duplicate observation in CSV')
                continue
            seen[key] = record
            batch.append(record)
        _insert_batch(conn, batch)
        count = conn.execute('SELECT count(*) FROM wasde_data WHERE csv_source_file=?',
                             (source_label,)).fetchone()[0]
        if count != len(batch):
            raise ValueError('CSV count does not match stored observations')
        conn.execute('INSERT OR IGNORE INTO csv_versions(source_file,sha256,raw_content) '
                     'VALUES (?,?,?)', (source_label, digest, content))
        conn.execute('INSERT INTO csv_sources(source_file,sha256,row_count) VALUES (?,?,?) '
                     'ON CONFLICT(source_file) DO UPDATE SET sha256=excluded.sha256, '
                     "row_count=excluded.row_count, loaded_at=datetime('now')",
                     (source_label, digest, count))
        conn.execute("INSERT INTO load_log(source,source_file,rows_loaded) VALUES ('csv',?,?)",
                     (source_label, count))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info('Published %d observations from %s (SHA-256 %s)', count, source_label, digest)
    return count


def _insert_batch(conn, batch):
    # Adopt matching legacy rows, but never steal rows owned by another CSV.
    cursor = conn.executemany('''INSERT INTO wasde_data
        (report_date,wasde_number,table_type_id,region_id,attribute,market_year,
         proj_est_flag,annual_quarter_flag,value,unit,unit_id,release_date,
         release_time,forecast_year,forecast_month,reliability_projection,csv_source_file)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT DO UPDATE SET value=excluded.value,
          wasde_number=excluded.wasde_number, proj_est_flag=excluded.proj_est_flag,
          annual_quarter_flag=excluded.annual_quarter_flag,
          reliability_projection=excluded.reliability_projection,
          unit_id=excluded.unit_id, release_date=excluded.release_date,
          release_time=excluded.release_time, forecast_year=excluded.forecast_year,
          forecast_month=excluded.forecast_month, csv_source_file=excluded.csv_source_file
        WHERE wasde_data.csv_source_file IS NULL
           OR wasde_data.csv_source_file=excluded.csv_source_file''', batch)
    if cursor.rowcount != len(batch):
        raise ValueError('Observation already belongs to another CSV source')


def load_csv_files(conn, csv_dir=None):
    target = Path(csv_dir) if csv_dir else CSV_DIR
    files = sorted(target.glob('oce-wasde-report-data-*.csv'))
    if not files:
        logger.warning('No CSV files found in %s', target)
        return 0
    total = 0
    for path in files:
        content = path.read_bytes()
        df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
        total += _load_dataframe(conn, df, path.name, raw_content=content)
    return total
