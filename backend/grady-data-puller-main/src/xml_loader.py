import logging
import xml.etree.ElementTree as ET
from datetime import datetime

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

XML_URL = "https://www.usda.gov/oce/commodity/wasde/wasde{mmyy}.xml"

# Sub-reports and their structure type
# "us" = U.S. reports (attributes x market_years, no region grouping)
# "world" = World reports (regions x attributes x market_years)
# "reliability" = different schema, skip in v1
REPORT_META = {
    "sr08": ("World and U.S. Supply and Use for Grains", "world"),
    "sr09": ("World and U.S. Supply and Use for Grains, Continued", "world"),
    "sr10": ("World and U.S. Supply and Use for Oilseeds", "world"),
    "sr11": ("U.S. Wheat Supply and Use", "us"),
    "sr12": ("U.S. Feed Grain and Corn Supply and Use", "us"),
    "sr13": ("U.S. Sorghum, Barley, and Oats Supply and Use", "us"),
    "sr14": ("U.S. Rice Supply and Use", "us"),
    "sr15": ("U.S. Soybeans and Products Supply and Use", "us"),
    "sr16": ("U.S. Sugar Supply and Use", "us"),
    "sr17": ("U.S. Cotton Supply and Use", "us"),
    "sr18": ("World Wheat Supply and Use", "world"),
    "sr19": ("World Wheat Supply and Use (Cont'd.)", "world"),
    "sr20": ("World Coarse Grain Supply and Use", "world"),
    "sr21": ("World Coarse Grain Supply and Use (Cont'd.)", "world"),
    "sr22": ("World Corn Supply and Use", "world"),
    "sr23": ("World Corn Supply and Use (Cont'd.)", "world"),
    "sr24": ("World Rice Supply and Use (Milled Basis)", "world"),
    "sr25": ("World Rice Supply and Use (Milled Basis) (Cont'd.)", "world"),
    "sr26": ("World Cotton Supply and Use", "world"),
    "sr27": ("World Cotton Supply and Use (Cont'd.)", "world"),
    "sr28": ("World Soybean Supply and Use", "world"),
    "sr29": ("World Soybean Meal Supply and Use", "world"),
    "sr30": ("World Soybean Oil Supply and Use", "world"),
    "sr31": ("U.S. Quarterly Animal Product Production", "us"),
    "sr32": ("U.S. Meats Supply and Use", "us"),
    "sr33": ("U.S. Egg Supply and Use", "us"),
    "sr34": ("U.S. Dairy Prices", "us"),
    "sr35": ("Reliability of Projections", "reliability"),
    "sr36": ("Reliability of Projections (Continued)", "reliability"),
    "sr37": ("Reliability of U.S. Projections", "reliability"),
}


def xml_url(month, year):
    mmyy = f"{month:02d}{year % 100:02d}"
    return XML_URL.format(mmyy=mmyy)


def _session_with_retries():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def download_xml(month, year):
    url = xml_url(month, year)
    logger.info("Downloading %s", url)
    session = _session_with_retries()
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    return resp.content


def _find_cell_value(cell_elem, prefixes=("cell_value",)):
    if cell_elem is None:
        return None
    for key, val in cell_elem.attrib.items():
        for prefix in prefixes:
            if key.startswith(prefix) and val.strip():
                return val.strip()
    return None


def _find_attr_name(elem, prefixes=("attribute",)):
    for key, val in elem.attrib.items():
        for prefix in prefixes:
            if key.startswith(prefix) and val.strip() and "filler" not in key.lower():
                return val.strip()
    return None


def _find_market_year(elem, prefixes=("market_year",)):
    for key, val in elem.attrib.items():
        for prefix in prefixes:
            if key.startswith(prefix):
                return val.strip()
    return None


def _find_forecast_month(elem, prefixes=("forecast_month",)):
    for key, val in elem.attrib.items():
        for prefix in prefixes:
            if key.startswith(prefix):
                return val.strip()
    return None


def _parse_value(raw):
    if raw is None or raw.strip() == "":
        return None
    raw = raw.strip().replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_year_month_values(year_group_collection):
    """Walk year_group_Collection -> year_group -> month_group_Collection -> Cell"""
    results = []
    if year_group_collection is None:
        return results
    for yg in year_group_collection:
        market_year = _find_market_year(yg)
        for mg_coll in yg:
            if "month_group_Collection" in mg_coll.tag:
                for mg in mg_coll:
                    forecast_month = _find_forecast_month(mg)
                    cell = mg.find("Cell")
                    cell_val = _find_cell_value(cell)
                    if cell_val is not None:
                        results.append((market_year, forecast_month, cell_val))
    return results


def parse_us_report(report_elem):
    """Parse U.S.-style report: matrix1 has m1_attribute_group_Collection with no region nesting."""
    rows = []
    for matrix in report_elem:
        if not matrix.tag.startswith("matrix"):
            continue
        # Find attribute group collections
        for child in matrix:
            if "attribute_group_Collection" in child.tag:
                for attr_group in child:
                    attribute_name = None
                    for elem in attr_group:
                        name = _find_attr_name(elem)
                        if name:
                            attribute_name = name
                            # Find the year_group_Collection inside this element
                            for sub in elem:
                                if "year_group_Collection" in sub.tag:
                                    for market_year, fm, val in _extract_year_month_values(sub):
                                        rows.append((attribute_name, "United States", market_year, fm, val))
                                    break
    return rows


def parse_world_report(report_elem):
    """Parse World-style report: matrix1 has m1_region_group_Collection with nested attributes."""
    rows = []
    for matrix in report_elem:
        if not matrix.tag.startswith("matrix"):
            continue
        for child in matrix:
            if "region_group_Collection" in child.tag:
                for region_group in child:
                    region_name = None
                    for key, val in region_group.attrib.items():
                        if key.startswith("region") and val.strip():
                            region_name = val.strip()
                            break
                    if not region_name:
                        continue
                    # Inside region_group, find attribute_group_Collection
                    for rg_child in region_group:
                        if "attribute_group_Collection" in rg_child.tag:
                            for attr_group in rg_child:
                                attribute_name = None
                                for elem in attr_group:
                                    name = _find_attr_name(elem)
                                    if name:
                                        attribute_name = name
                                        for sub in elem:
                                            if "year_group_Collection" in sub.tag:
                                                for market_year, fm, val in _extract_year_month_values(sub):
                                                    rows.append((attribute_name, region_name, market_year, fm, val))
                                                break
    return rows


def load_xml_report(conn, month, year):
    mmyy = f"{month:02d}{year % 100:02d}"
    source_file = f"wasde{mmyy}.xml"

    if already_loaded(conn, source_file):
        logger.info("Skipping %s (already loaded)", source_file)
        return 0

    xml_bytes = download_xml(month, year)
    root = ET.fromstring(xml_bytes)

    report_date_str = None
    wasde_number = None
    total_rows = 0
    batch = []

    # Cache
    table_type_cache = {}
    region_cache = {}
    unit_cache = {}

    for sr_tag in root:
        sr_name = sr_tag.tag  # e.g. "sr11"
        meta = REPORT_META.get(sr_name)
        if meta is None:
            continue
        _, structure = meta
        if structure == "reliability":
            continue

        report_elem = sr_tag.find("Report")
        if report_elem is None:
            continue

        # Use the actual title from the XML
        report_title = (report_elem.get("sub_report_title") or "").strip()
        if not report_title:
            report_title = meta[0]

        # Clean footnote markers from title
        for suffix in (" 1/", " 2/", " 3/"):
            report_title = report_title.replace(suffix, "").strip()

        report_month = report_elem.get("Report_Month", "")
        page_title = report_elem.get("page_title", "")

        # Extract WASDE number from page_title like "WASDE - 669 - 11"
        if wasde_number is None and page_title:
            parts = page_title.split("-")
            if len(parts) >= 2:
                try:
                    wasde_number = int(parts[1].strip())
                except ValueError:
                    pass

        # Build report_date from report_month (e.g. "March 2026")
        if report_date_str is None and report_month:
            try:
                dt = datetime.strptime(report_month, "%B %Y")
                report_date_str = dt.strftime("%Y-%m-%d")
            except ValueError:
                report_date_str = report_month

        # Determine scope
        scope = "World" if structure == "world" else "U.S."

        # Parse
        if structure == "us":
            parsed = parse_us_report(report_elem)
        else:
            parsed = parse_world_report(report_elem)

        # Subtitle may have unit info
        unit = (report_elem.get("sub_report_subtitle") or "").strip() or None
        if unit:
            # Clean parentheses: "(Million Metric Tons)" -> "Million Metric Tons"
            unit = unit.strip("() ")

        for attribute, region_name, market_year, forecast_month, raw_val in parsed:
            if not attribute or not market_year:
                continue

            # Resolve table_type
            if report_title not in table_type_cache:
                tt_id = get_or_create_table_type(conn, report_title, None, scope)
                table_type_cache[report_title] = tt_id
            table_type_id = table_type_cache[report_title]

            # Resolve region
            if region_name not in region_cache:
                r_id = get_or_create_region(conn, region_name)
                region_cache[region_name] = r_id
            region_id = region_cache[region_name]

            # Resolve unit
            if unit not in unit_cache:
                unit_cache[unit] = get_unit_id(conn, unit)
            unit_id = unit_cache[unit]

            value = _parse_value(raw_val)

            batch.append((
                report_date_str,
                wasde_number,
                table_type_id,
                region_id,
                attribute,
                market_year,
                None,  # proj_est_flag (not in XML)
                None,  # annual_quarter_flag
                value,
                unit,
                unit_id,
                None,  # release_date
                None,  # release_time
                year,  # forecast_year
                month,  # forecast_month
            ))

            if len(batch) >= 5000:
                _insert_batch(conn, batch)
                total_rows += len(batch)
                batch = []

    if batch:
        _insert_batch(conn, batch)
        total_rows += len(batch)

    log_load(conn, "xml", source_file, total_rows)
    logger.info("Loaded %d rows from %s", total_rows, source_file)
    return total_rows


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
