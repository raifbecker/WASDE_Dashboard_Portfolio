import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.db import (
    already_loaded,
    get_or_create_region,
    get_or_create_table_type,
    get_unit_id,
    log_load,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CSV_DIR = DATA_DIR / "csv"

# CSV column names from USDA CSVs
COL_WASDE_NUMBER = "WasdeNumber"
COL_REPORT_DATE = "ReportDate"
COL_REPORT_TITLE = "ReportTitle"
COL_ATTRIBUTE = "Attribute"
COL_COMMODITY = "Commodity"
COL_REGION = "Region"
COL_MARKET_YEAR = "MarketYear"
COL_PROJ_EST = "ProjEstFlag"
COL_AQ_FLAG = "AnnualQuarterFlag"
COL_VALUE = "Value"
COL_UNIT = "Unit"
COL_RELEASE_DATE = "ReleaseDate"
COL_RELEASE_TIME = "ReleaseTime"
COL_FORECAST_YEAR = "ForecastYear"
COL_FORECAST_MONTH = "ForecastMonth"


def normalize_report_date(raw):
    """Convert 'January 2021' or similar to '2021-01-01'. Pass through if already YYYY-MM-DD."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if len(raw) == 10 and raw[4] == '-':
        return raw
    for fmt in ("%B %Y", "%b %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def parse_value(raw):
    if raw is None:
        return None
    raw = str(raw).strip().replace(",", "")
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_int(raw):
    if raw is None:
        return None
    raw = str(raw).strip()
    if raw == "":
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def infer_scope(report_title):
    title = report_title.strip()
    if title.startswith("U.S.") or title.startswith("US "):
        return "U.S."
    if "World" in title or "Foreign" in title:
        return "World"
    return None


def _load_dataframe(conn, df, source_label):
    """Load rows from a pandas DataFrame into the database."""
    if already_loaded(conn, source_label):
        logger.info("Skipping %s (already loaded)", source_label)
        return 0

    table_type_cache = {}
    region_cache = {}
    unit_cache = {}
    rows_loaded = 0
    batch = []

    for _, row in df.iterrows():
        report_title = str(row.get(COL_REPORT_TITLE, "") or "").strip()
        commodity = str(row.get(COL_COMMODITY, "") or "").strip() or None
        region_name = str(row.get(COL_REGION, "") or "").strip() or "United States"

        if not report_title:
            continue

        table_type_key = (report_title, commodity)
        if table_type_key not in table_type_cache:
            scope = infer_scope(report_title)
            tt_id = get_or_create_table_type(conn, report_title, commodity, scope)
            table_type_cache[table_type_key] = tt_id
        table_type_id = table_type_cache[table_type_key]

        if region_name not in region_cache:
            r_id = get_or_create_region(conn, region_name)
            region_cache[region_name] = r_id
        region_id = region_cache[region_name]

        unit_str = str(row.get(COL_UNIT, "") or "").strip() or None
        if unit_str not in unit_cache:
            unit_cache[unit_str] = get_unit_id(conn, unit_str)
        unit_id = unit_cache[unit_str]

        batch.append((
            normalize_report_date(str(row.get(COL_REPORT_DATE, "") or "").strip()),
            parse_int(row.get(COL_WASDE_NUMBER)),
            table_type_id,
            region_id,
            str(row.get(COL_ATTRIBUTE, "") or "").strip(),
            str(row.get(COL_MARKET_YEAR, "") or "").strip(),
            str(row.get(COL_PROJ_EST, "") or "").strip() or None,
            str(row.get(COL_AQ_FLAG, "") or "").strip() or None,
            parse_value(row.get(COL_VALUE)),
            unit_str,
            unit_id,
            str(row.get(COL_RELEASE_DATE, "") or "").strip() or None,
            str(row.get(COL_RELEASE_TIME, "") or "").strip() or None,
            parse_int(row.get(COL_FORECAST_YEAR)),
            parse_int(row.get(COL_FORECAST_MONTH)),
        ))

        if len(batch) >= 5000:
            _insert_batch(conn, batch)
            rows_loaded += len(batch)
            batch = []

    if batch:
        _insert_batch(conn, batch)
        rows_loaded += len(batch)

    log_load(conn, "csv", source_label, rows_loaded)
    logger.info("Loaded %d rows from %s", rows_loaded, source_label)
    return rows_loaded


def _insert_batch(conn, batch):
    conn.executemany(
        """INSERT OR IGNORE INTO wasde_data
           (report_date, wasde_number, table_type_id, region_id,
            attribute, market_year, proj_est_flag, annual_quarter_flag,
            value, unit, unit_id, release_date, release_time,
            forecast_year, forecast_month)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        batch,
    )
    conn.commit()


def load_csv_files(conn, csv_dir=None):
    """Load all individual CSV files from data/csv/ into the database."""
    target_dir = Path(csv_dir) if csv_dir else CSV_DIR
    csv_files = sorted(target_dir.glob("oce-wasde-report-data-*.csv"))
    if not csv_files:
        logger.warning("No CSV files found in %s", target_dir)
        return 0

    total = 0
    for csv_file in csv_files:
        source_label = csv_file.name
        if already_loaded(conn, source_label):
            logger.info("Skipping %s (already loaded)", source_label)
            continue
        df = pd.read_csv(csv_file, dtype=str)
        rows = _load_dataframe(conn, df, source_label)
        total += rows

    return total
