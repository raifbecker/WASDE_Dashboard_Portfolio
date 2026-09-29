"""
Parse WASDE plain-text (.txt) reports downloaded from
https://www.usda.gov/oce/commodity/wasde/wasdeMMYY.txt

The TXT file contains multiple page-based tables. Each visual page starts
with a "WASDE - NNN - PP" header line. Within each page, ======= lines
serve as top/bottom borders and header separators.

This parser focuses on **U.S. detail pages** (pp 11-17) which have a clean
4-column layout mapping directly to the wasde_data schema.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.db import (
    already_loaded,
    get_or_create_region,
    get_or_create_table_type,
    get_unit_id,
    log_load,
)

logger = logging.getLogger(__name__)

TXT_URL = "https://www.usda.gov/oce/commodity/wasde/wasde{mmyy}.txt"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TXT_DIR = DATA_DIR / "txt"


def txt_url(month, year):
    mmyy = f"{month:02d}{year % 100:02d}"
    return TXT_URL.format(mmyy=mmyy)


def _session_with_retries():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def download_txt(month, year):
    url = txt_url(month, year)
    logger.info("Downloading %s", url)
    session = _session_with_retries()
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    return resp.text


def load_txt_file(path):
    """Load a local .txt file."""
    return Path(path).read_text()


def _parse_value(raw):
    if raw is None:
        return None
    raw = raw.strip().replace(",", "")
    if raw in ("", "3/", "2/", "1/", "4/", "5/", "6/", "7/", "8/", "9/", "10/"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _clean_footnotes(text):
    """Remove footnote markers like  1/  2/  etc. from text."""
    return re.sub(r'\s+\d+/', '', text).strip()


# --------------------------------------------------------------------------
# Page splitting — split by WASDE header lines
# --------------------------------------------------------------------------

def _split_pages(text):
    """Split the TXT file into logical pages, each starting with 'WASDE - NNN - PP'."""
    lines = text.splitlines()
    pages = []
    current_page = []

    for line in lines:
        if re.search(r'WASDE\s*-\s*\d+\s*-\s*\d+', line):
            if current_page:
                pages.append(current_page)
            current_page = [line]
        else:
            current_page.append(line)

    if current_page:
        pages.append(current_page)

    return pages


def _extract_page_meta(page_lines):
    """Extract WASDE number, page number, report month, and title from a page."""
    wasde_number = None
    page_number = None
    report_month = None
    title = None

    for line in page_lines[:3]:
        m = re.search(r'WASDE\s*-\s*(\d+)\s*-\s*(\d+)', line)
        if m:
            wasde_number = int(m.group(1))
            page_number = int(m.group(2))
        m2 = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', line)
        if m2:
            report_month = f"{m2.group(1)} {m2.group(2)}"

    # Title: look for the descriptive line after the WASDE header
    for line in page_lines[1:10]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("="):
            continue
        # Skip unit lines, column headers
        if re.match(r'^\(?[Mm]illion|^\(?[Tt]housand|^Bushels|^Pounds|^1000|^Billion', stripped):
            continue
        if re.match(r'^\d{4}/\d{2}', stripped):
            continue
        if re.match(r'^Item\s', stripped):
            continue
        if re.search(r'(Supply and Use|Projections|Production|Prices|Consumption)', stripped):
            title = _clean_footnotes(stripped)
            # Strip (Contd.) so continuation pages merge with the base title
            title = re.sub(r'\s*\(Contd\.\)', '', title).strip()
            break

    return wasde_number, page_number, report_month, title


# --------------------------------------------------------------------------
# U.S. detail page parser (pages 11-17)
# --------------------------------------------------------------------------

def _is_us_detail_page(page_lines):
    """Detect if this page is a U.S.-style detail page (4-column layout)."""
    for line in page_lines:
        if re.search(r'\d{4}/\d{2}\s+\d{4}/\d{2}\s+Est\.', line):
            return True
    return False


def _parse_us_detail_page(page_lines, report_date, wasde_number, title, forecast_year, forecast_month):
    """Parse a U.S.-style detail page with 4 value columns."""
    rows = []
    current_unit = None
    current_commodity = None

    # Find market year columns from header
    market_years = []
    proj_est_flags = []
    data_start_idx = 0

    for idx, line in enumerate(page_lines):
        parts = re.findall(r'(\d{4}/\d{2})', line)
        if len(parts) >= 4 and 'Est.' in line and 'Proj.' in line:
            market_years = parts[:4]
            proj_est_flags = [None, "Est.", "Proj.", "Proj."]
            data_start_idx = idx + 1
            break

    if not market_years:
        return rows

    # Skip past the "Item ... Mar ... Apr" line and the separator
    for idx in range(data_start_idx, min(data_start_idx + 5, len(page_lines))):
        line = page_lines[idx]
        if line.strip().startswith("="):
            data_start_idx = idx + 1
            break

    for line in page_lines[data_start_idx:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("="):
            continue

        # Unit lines embedded in the data section
        if re.match(r'^(Million\s|Thousand\s|1000\s|Bushels?\s*$|Pounds?\s*$|Metric Tons\s*$|'
                     r'Billion\s|Dol|Cents|mil\.\s|rough\s)', stripped):
            current_unit = _clean_footnotes(stripped)
            continue

        # Commodity sub-header (all-caps, no numbers — like "CORN", "SORGHUM", etc.)
        if (re.match(r'^[A-Z][A-Z &.,/\'-]+$', stripped)
                and not re.search(r'\d', stripped)
                and len(stripped) < 40):
            current_commodity = stripped
            continue

        # Data row: attribute text followed by numeric values
        m = re.match(r'^(.+?)\s{2,}([-\d].*)$', line)
        if not m:
            continue

        attr_raw = m.group(1).rstrip()
        values_section = m.group(2)

        attribute = _clean_footnotes(attr_raw).strip()
        if not attribute:
            continue

        # Split values by 2+ whitespace
        values = re.split(r'\s{2,}', values_section.strip())

        while len(values) < len(market_years):
            values.append(None)

        for i, val_str in enumerate(values[:len(market_years)]):
            value = _parse_value(val_str)
            if value is None:
                continue

            rows.append((
                report_date,
                wasde_number,
                title,
                "United States",
                attribute,
                market_years[i],
                proj_est_flags[i],
                None,  # annual_quarter_flag
                value,
                current_unit,
                forecast_year,
                forecast_month,
                current_commodity,
            ))

    return rows


# --------------------------------------------------------------------------
# World detail page parser (pages 18-30)
# --------------------------------------------------------------------------

_WORLD_ATTRIBUTES_7 = [
    "Beginning Stocks", "Production", "Imports", "Feed", "Domestic Total", "Exports", "Ending Stocks"
]
_WORLD_ATTRIBUTES_6 = [
    "Beginning Stocks", "Production", "Imports", "Domestic Total", "Exports", "Ending Stocks"
]


def _is_world_detail_page(page_lines):
    """Detect world detail pages."""
    for line in page_lines[:8]:
        if re.search(r'World\s+(Wheat|Coarse|Corn|Rice|Cotton|Soybean)', line):
            return True
    return False


def _detect_world_attributes(page_lines):
    """Determine column attributes from the header lines."""
    header_text = " ".join(line for line in page_lines[:15])
    if 'Feed' in header_text and 'Total' in header_text and 'Exports' in header_text:
        return _WORLD_ATTRIBUTES_7
    if 'Loss' in header_text:
        return ["Beginning Stocks", "Production", "Imports", "Domestic", "Exports", "Loss", "Ending Stocks"]
    return _WORLD_ATTRIBUTES_6


def _parse_world_detail_page(page_lines, report_date, wasde_number, title, forecast_year, forecast_month):
    """Parse a world-style detail page with region rows and supply/use columns."""
    rows = []
    attributes = _detect_world_attributes(page_lines)
    num_cols = len(attributes)

    current_unit = None
    current_market_year = None
    current_proj_est = None
    in_proj_section = False
    current_region = None

    # Find unit from header
    for line in page_lines:
        stripped = line.strip().strip("()")
        if re.match(r'^Million\s|^Thousand\s|^1000\s', stripped):
            current_unit = _clean_footnotes(stripped)
            break

    # Find where data starts (after the last ===== line in header area)
    data_start = 0
    sep_count = 0
    for idx, line in enumerate(page_lines):
        if line.strip().startswith("=" * 30):
            sep_count += 1
            data_start = idx + 1
            if sep_count >= 2:
                break

    for line in page_lines[data_start:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("="):
            continue

        # Unit line mid-page
        if re.match(r'^\s*\(?(Million\s+\S+|Thousand\s+\S+)', stripped):
            current_unit = _clean_footnotes(stripped).strip("() ")
            continue

        # Market year section: "                                    2023/24"
        year_match = re.match(r'^\s{10,}(\d{4}/\d{2})\s*(Est\.)?\s*(Proj\.)?\s*$', line)
        if year_match:
            current_market_year = year_match.group(1)
            if year_match.group(2):
                current_proj_est = "Est."
                in_proj_section = False
            elif year_match.group(3):
                current_proj_est = "Proj."
                in_proj_section = True
            else:
                current_proj_est = None
                in_proj_section = False
            current_region = None
            continue

        if not current_market_year:
            continue

        if in_proj_section:
            # Month-prefixed data line
            month_match = re.match(r'^\s+(Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Jan|Feb)\s+([-\d][\d.\s-]+)$', line)
            if month_match and current_region:
                values = month_match.group(2).split()
                for i, val_str in enumerate(values[:num_cols]):
                    value = _parse_value(val_str)
                    if value is not None:
                        rows.append((
                            report_date, wasde_number, title, current_region,
                            attributes[i], current_market_year, current_proj_est,
                            None, value, current_unit, forecast_year, forecast_month,
                            None,
                        ))
                continue

            # Region with values on same line
            data_match = re.match(r'^(.+?)\s{2,}([-\d][\d.\s-]+)$', line)
            if data_match:
                region_raw = _clean_footnotes(data_match.group(1).strip())
                values = data_match.group(2).split()
                if region_raw and not re.match(r'^\d', region_raw):
                    current_region = region_raw
                    for i, val_str in enumerate(values[:num_cols]):
                        value = _parse_value(val_str)
                        if value is not None:
                            rows.append((
                                report_date, wasde_number, title, current_region,
                                attributes[i], current_market_year, current_proj_est,
                                None, value, current_unit, forecast_year, forecast_month,
                                None,
                            ))
                continue

            # Standalone region name (no values, month lines follow)
            region_only = re.match(r'^(\S.+?)\s*$', line)
            if region_only:
                name = _clean_footnotes(region_only.group(1).strip())
                if name and not re.match(r'^\d', name) and name not in ('Selected Other', 'WASDE'):
                    current_region = name

        else:
            # Non-projection section: "Region  val1  val2  ..."
            data_match = re.match(r'^(.+?)\s{2,}([-\d][\d.\s-]+)$', line)
            if data_match:
                region_raw = _clean_footnotes(data_match.group(1).strip())
                values = data_match.group(2).split()
                if not region_raw or re.match(r'^\d', region_raw):
                    continue
                if region_raw in ('Supply', 'Use', 'Region', 'Selected Other'):
                    continue

                for i, val_str in enumerate(values[:num_cols]):
                    value = _parse_value(val_str)
                    if value is not None:
                        rows.append((
                            report_date, wasde_number, title, region_raw,
                            attributes[i], current_market_year, current_proj_est,
                            None, value, current_unit, forecast_year, forecast_month,
                            None,
                        ))

    return rows


# --------------------------------------------------------------------------
# Main entry points
# --------------------------------------------------------------------------

def parse_txt_report(text, forecast_year=None, forecast_month=None):
    """Parse a full WASDE TXT report into a list of row tuples."""
    pages = _split_pages(text)
    all_rows = []

    # Extract report-level metadata from first page
    wasde_number = None
    report_month = None
    for page in pages[:3]:
        wn, pn, rm, _ = _extract_page_meta(page)
        if wn:
            wasde_number = wn
        if rm:
            report_month = rm
        if wasde_number and report_month:
            break

    report_date = None
    if report_month:
        try:
            dt = datetime.strptime(report_month, "%B %Y")
            report_date = dt.strftime("%Y-%m-%d")
            if not forecast_year:
                forecast_year = dt.year
            if not forecast_month:
                forecast_month = dt.month
        except ValueError:
            report_date = report_month

    for page in pages:
        wn, pn, _, title = _extract_page_meta(page)
        if not title:
            continue

        # Skip reliability pages
        if 'Reliability' in title:
            continue

        page_wn = wn or wasde_number

        if _is_us_detail_page(page):
            page_rows = _parse_us_detail_page(
                page, report_date, page_wn, title,
                forecast_year, forecast_month,
            )
            all_rows.extend(page_rows)
        elif _is_world_detail_page(page):
            page_rows = _parse_world_detail_page(
                page, report_date, page_wn, title,
                forecast_year, forecast_month,
            )
            all_rows.extend(page_rows)

    logger.info("Parsed %d rows from TXT report", len(all_rows))
    return all_rows


def load_txt_report(conn, month, year, txt_path=None, target_table="wasde_data"):
    """Download (or load local) TXT report and insert into the database."""
    mmyy = f"{month:02d}{year % 100:02d}"
    source_label = f"txt-rough:wasde{mmyy}.txt" if target_table == "wasde_data_rough" else f"txt:wasde{mmyy}.txt"

    if already_loaded(conn, source_label):
        logger.info("Skipping %s (already loaded)", source_label)
        return 0

    if txt_path:
        text = load_txt_file(txt_path)
    else:
        text = download_txt(month, year)
        TXT_DIR.mkdir(parents=True, exist_ok=True)
        (TXT_DIR / f"wasde{mmyy}.txt").write_text(text)

    parsed = parse_txt_report(text, forecast_year=year, forecast_month=month)
    if not parsed:
        logger.warning("No rows parsed from wasde%s.txt", mmyy)
        return 0

    table_type_cache = {}
    region_cache = {}
    unit_cache = {}
    batch = []
    total_rows = 0

    for row in parsed:
        (report_date, wasde_number, report_title, region_name, attribute,
         market_year, proj_est_flag, annual_quarter_flag, value, unit_str,
         fy, fm, _commodity) = row

        if not report_title or not attribute or not market_year:
            continue

        if report_title not in table_type_cache:
            scope = "World" if "World" in report_title else "U.S."
            # Reuse existing table_type by report_title (any commodity)
            existing = conn.execute(
                "SELECT id FROM table_types WHERE report_title = ? AND commodity IS NOT NULL LIMIT 1",
                (report_title,),
            ).fetchone()
            if existing:
                tt_id = existing[0]
            else:
                tt_id = get_or_create_table_type(conn, report_title, None, scope)
            table_type_cache[report_title] = tt_id
        table_type_id = table_type_cache[report_title]

        if region_name not in region_cache:
            r_id = get_or_create_region(conn, region_name)
            region_cache[region_name] = r_id
        region_id = region_cache[region_name]

        if unit_str not in unit_cache:
            unit_cache[unit_str] = get_unit_id(conn, unit_str)
        unit_id = unit_cache[unit_str]

        batch.append((
            report_date,
            wasde_number,
            table_type_id,
            region_id,
            attribute,
            market_year,
            proj_est_flag,
            annual_quarter_flag,
            value,
            unit_str,
            unit_id,
            None,  # release_date
            None,  # release_time
            fy,
            fm,
        ))

        if len(batch) >= 5000:
            _insert_batch(conn, batch, target_table)
            total_rows += len(batch)
            batch = []

    if batch:
        _insert_batch(conn, batch, target_table)
        total_rows += len(batch)

    if total_rows == 0:
        logger.warning("No rows inserted from wasde%s.txt — skipping load_log entry", mmyy)
        return 0

    log_load(conn, "txt", source_label, total_rows)
    logger.info("Loaded %d rows from wasde%s.txt into %s", total_rows, mmyy, target_table)
    return total_rows


def _insert_batch(conn, batch, table="wasde_data"):
    conn.executemany(
        f"""INSERT OR IGNORE INTO {table}
           (report_date, wasde_number, table_type_id, region_id,
            attribute, market_year, proj_est_flag, annual_quarter_flag,
            value, unit, unit_id, release_date, release_time,
            forecast_year, forecast_month)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        batch,
    )
    conn.commit()


def promote_rough(conn, month, year, txt_path=None):
    """Load corrected data from TXT to main table.

    Rough data is preserved in wasde_data_rough for comparison via v_rough_vs_final.
    """
    rows = load_txt_report(conn, month, year, txt_path=txt_path, target_table="wasde_data")
    logger.info("Promoted %d rows to wasde_data. Rough data preserved for comparison.", rows)
    return rows
