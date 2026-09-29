import logging
import re
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

ESMIS_BASE = "https://esmis.nal.usda.gov/api/v1"

# Major crop sections to parse, mapping header pattern to (report_title, commodity)
CROP_SECTIONS = {
    r"^Corn Area Planted\b": ("Prospective Plantings", "Corn"),
    r"^Soybean Area Planted\b": ("Prospective Plantings", "Soybeans"),
    r"^All Wheat Area Planted\b": ("Prospective Plantings", "All Wheat"),
    r"^Winter Wheat Area Planted\b": ("Prospective Plantings", "Winter Wheat"),
    r"^Durum\s+Wheat Area Planted\b": ("Prospective Plantings", "Durum Wheat"),
    r"^Other Spring Wheat\s*\n?\s*Area Planted\b": ("Prospective Plantings", "Other Spring Wheat"),
    r"^Cotton Area Planted by Type\b": ("Prospective Plantings", "Cotton"),
}

# Cotton sub-type labels that appear within the Cotton table block
COTTON_SUBTYPES = {
    "Upland": ("Prospective Plantings", "Upland Cotton"),
    "American Pima": ("Prospective Plantings", "American Pima Cotton"),
    "All": ("Prospective Plantings", "All Cotton"),
}

UNIT_ACRES = "1,000 Acres"


def _session_with_retries():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def fetch_releases(identifier, start_year=2010):
    """Fetch all releases for a given ESMIS publication identifier, filtered by start_year."""
    session = _session_with_retries()
    releases = []
    page = 0

    while True:
        url = f"{ESMIS_BASE}/release/findByIdentifier/{identifier}"
        resp = session.get(url, params={"page": page}, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("results", []):
            release_dt = item.get("release_datetime", "")
            if not release_dt:
                continue
            try:
                # Normalize timezone: Z -> +00:00, +0000 -> +00:00
                normalized = release_dt.replace("Z", "+00:00")
                normalized = re.sub(r'\+(\d{2})(\d{2})$', r'+\1:\2', normalized)
                dt = datetime.fromisoformat(normalized)
            except ValueError:
                continue

            if dt.year < start_year:
                continue

            # Find the .txt file URL
            txt_url = None
            for f in item.get("files", []):
                if f.endswith(".txt"):
                    txt_url = f
                    break

            if txt_url:
                release_date = dt.strftime("%Y-%m-%d")
                releases.append((release_date, txt_url))

        pager = data.get("pager", {})
        current = pager.get("current_page", 0)
        total_pages = pager.get("total_pages", 1)
        if current + 1 >= total_pages:
            break
        page += 1

    releases.sort(key=lambda x: x[0])
    return releases


def download_report_txt(url):
    """Download a .txt report file and return its text content."""
    session = _session_with_retries()
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    return resp.text


def _parse_value(raw):
    """Parse a numeric value from a report cell."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or raw == "-" or raw in ("(NA)", "(X)", "(D)", "(Z)"):
        return None
    # Strip footnote markers like "1/"
    raw = re.sub(r'\s*\d+/$', '', raw)
    raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _clean_state_name(raw):
    """Extract state name from a dotted leader line, e.g. 'Alabama ...............'"""
    # Strip dots and trailing whitespace
    name = re.sub(r'\.+$', '', raw.strip()).strip()
    # Handle multi-word states that may be split oddly
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _extract_year_columns(header_line):
    """Extract year integers from a header line like '2024  :  2025  :  2026 1/'"""
    years = []
    for m in re.finditer(r'(\d{4})\s*(?:\d+/)?', header_line):
        years.append(int(m.group(1)))
    return years


def _find_section_header(line, next_line=""):
    """Check if a line matches one of our crop section headers.
    Returns (report_title, commodity) tuple or None.
    Some headers span two lines (e.g., 'Other Spring Wheat\\nArea Planted').
    Skips table-of-contents lines (which contain '......' dots).
    """
    # Skip TOC lines that have dot leaders (e.g., "Corn Area Planted ... 6")
    if "......" in line:
        return None
    combined = line + "\n" + next_line
    for pattern, title_tuple in CROP_SECTIONS.items():
        if re.search(pattern, line, re.IGNORECASE):
            return title_tuple
        if "......" not in next_line and re.search(pattern, combined, re.IGNORECASE):
            return title_tuple
    return None


def parse_report_txt(text, release_date):
    """Parse a Prospective Plantings .txt report into structured rows.

    Returns list of (report_title, commodity, state, attribute, market_year_str, value, unit).
    """
    lines = text.split("\n")
    rows = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""

        section_match = _find_section_header(line, next_line)
        if section_match is None:
            i += 1
            continue

        report_title, commodity = section_match
        is_cotton = commodity == "Cotton"

        # Found a section — now find the year columns and data rows
        year_cols = []
        j = i + 1
        # Scan forward to find the year header and dash separator
        while j < min(i + 30, len(lines)):
            candidate = lines[j]
            years = _extract_year_columns(candidate)
            if len(years) >= 2:
                year_cols = years
                break
            j += 1

        if not year_cols:
            i += 1
            continue

        # Skip to the data rows: advance past the second dashed separator
        # (first separator is before year header, second is after it)
        dash_count = 0
        j += 1
        while j < min(i + 50, len(lines)):
            if re.match(r'^\s*-{20,}\s*$', lines[j].strip()):
                dash_count += 1
                if dash_count >= 1:
                    j += 1
                    break
            j += 1

        # Skip unit description lines and blanks before first data row
        while j < min(i + 60, len(lines)):
            stripped_j = lines[j].strip()
            if not stripped_j:
                j += 1
                continue
            if "1,000 acres" in stripped_j.lower() or "percent" in stripped_j.lower():
                j += 1
                continue
            # Skip lines that are just ": " with no state name
            if re.match(r'^\s+:', stripped_j):
                j += 1
                continue
            break

        # Now parse the state data rows
        current_cotton_subtype = None
        while j < len(lines):
            raw = lines[j]

            # Check for end of section
            if re.match(r'^\s*[-]{40,}', raw):
                # Could be end separator — peek ahead for footnotes or new section
                j += 1
                # Skip footnotes
                while j < len(lines) and (
                    lines[j].strip().startswith("1/")
                    or lines[j].strip().startswith("2/")
                    or lines[j].strip().startswith("3/")
                    or lines[j].strip().startswith("-")
                    or lines[j].strip().startswith("(")
                    or lines[j].strip() == ""
                    or lines[j].strip().startswith("See footnote")
                ):
                    j += 1
                break

            # Skip blank lines and header-like lines within the section
            stripped = raw.strip()
            if not stripped:
                j += 1
                continue

            # Skip "percent" column header lines
            if "percent" in stripped.lower() and ":" not in stripped:
                j += 1
                continue

            # Skip lines that are just column headers
            if "1,000 acres" in stripped.lower():
                j += 1
                continue

            # Cotton sub-type detection
            if is_cotton:
                # Check for sub-type headers like "Upland    :" or "American Pima    :"
                # These lines have a label, optional spaces, colon, and nothing after
                if ":" in raw:
                    colon_pos = raw.index(":")
                    before_colon = raw[:colon_pos].strip()
                    after_colon = raw[colon_pos + 1:].strip()
                    if not after_colon or not re.search(r'\d', after_colon):
                        label_lower = before_colon.lower()
                        if label_lower == "upland" or label_lower.startswith("upland "):
                            current_cotton_subtype = "Upland"
                            j += 1
                            continue
                        elif label_lower.startswith("american pima"):
                            current_cotton_subtype = "American Pima"
                            j += 1
                            continue
                        elif label_lower == "all" or label_lower.startswith("all "):
                            if "wheat" not in label_lower:
                                current_cotton_subtype = "All"
                                j += 1
                                continue

            # Parse a state data row — must contain ":"
            if ":" not in raw:
                j += 1
                continue

            colon_idx = raw.index(":")
            state_part = raw[:colon_idx]
            values_part = raw[colon_idx + 1:]

            state_name = _clean_state_name(state_part)
            if not state_name:
                j += 1
                continue

            # Skip non-state header lines
            if state_name.lower() in ("state", "type and state", "class and state",
                                       "varietal type", "class, type, and state",
                                       "size and state"):
                j += 1
                continue

            # Parse numeric values from the right side
            # Split on whitespace to get value tokens
            tokens = values_part.split()

            # Filter out "percent" column (last column if it has 1 more than year_cols)
            # We expect len(year_cols) values, possibly with a percent column
            numeric_tokens = []
            for tok in tokens:
                # Skip pure text tokens
                cleaned = re.sub(r'\s*\d+/$', '', tok.strip())
                if cleaned in ("", "(X)", "(NA)"):
                    numeric_tokens.append(cleaned)
                    continue
                if cleaned == "-":
                    numeric_tokens.append(cleaned)
                    continue
                # Check if it looks numeric
                try:
                    float(cleaned.replace(",", ""))
                    numeric_tokens.append(tok)
                except ValueError:
                    continue

            # Take only as many values as we have year columns
            # The last token may be the "percent" column — skip it if we have too many
            if len(numeric_tokens) > len(year_cols):
                numeric_tokens = numeric_tokens[:len(year_cols)]

            # Determine the (report_title, commodity) for this row
            if is_cotton and current_cotton_subtype:
                row_report, row_commodity = COTTON_SUBTYPES.get(
                    current_cotton_subtype,
                    ("Prospective Plantings", "All Cotton"),
                )
            elif is_cotton:
                # Before any sub-type header, skip
                j += 1
                continue
            else:
                row_report, row_commodity = report_title, commodity

            for k, val_str in enumerate(numeric_tokens):
                if k >= len(year_cols):
                    break
                value = _parse_value(val_str)
                year_str = str(year_cols[k])
                rows.append((
                    row_report,
                    row_commodity,
                    state_name,
                    "Area Planted",
                    year_str,
                    value,
                    UNIT_ACRES,
                ))

            j += 1

        i = j if j > i else i + 1

    return rows


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


def load_prospective_plantings(conn, start_year=2010):
    """Load NASS Prospective Plantings data from the ESMIS API into SQLite."""
    logger.info("Fetching Prospective Plantings releases (from %d)...", start_year)
    releases = fetch_releases("ProsPlan", start_year=start_year)
    logger.info("Found %d releases", len(releases))

    total_rows = 0
    table_type_cache = {}
    region_cache = {}
    unit_id = None  # resolved once

    for release_date, txt_url in releases:
        source_file = f"ProsPlan-{release_date}"

        if already_loaded(conn, source_file):
            logger.info("Skipping %s (already loaded)", source_file)
            continue

        logger.info("Downloading %s ...", txt_url)
        try:
            text = download_report_txt(txt_url)
        except Exception:
            logger.warning("Failed to download %s, skipping", txt_url, exc_info=True)
            continue

        logger.info("Parsing %s ...", source_file)
        try:
            parsed = parse_report_txt(text, release_date)
        except Exception:
            logger.warning("Failed to parse %s, skipping", source_file, exc_info=True)
            continue

        if not parsed:
            logger.warning("No data parsed from %s", source_file)
            continue

        # Resolve unit_id once
        if unit_id is None:
            unit_id = get_unit_id(conn, UNIT_ACRES)

        batch = []
        release_year = int(release_date[:4])
        release_month = int(release_date[5:7])

        for report_title, commodity, state_name, attribute, year_str, value, unit_str in parsed:
            # Resolve table_type
            tt_key = (report_title, commodity)
            if tt_key not in table_type_cache:
                tt_id = get_or_create_table_type(conn, report_title, commodity, "U.S.")
                table_type_cache[tt_key] = tt_id
            table_type_id = table_type_cache[tt_key]

            # Resolve region
            if state_name not in region_cache:
                r_id = get_or_create_region(conn, state_name)
                region_cache[state_name] = r_id
            region_id = region_cache[state_name]

            batch.append((
                release_date,
                None,           # wasde_number (not applicable)
                table_type_id,
                region_id,
                attribute,
                year_str,       # market_year = crop year
                None,           # proj_est_flag
                None,           # annual_quarter_flag
                value,
                unit_str,
                unit_id,
                release_date,   # release_date
                None,           # release_time
                release_year,   # forecast_year
                release_month,  # forecast_month
            ))

            if len(batch) >= 5000:
                _insert_batch(conn, batch)
                total_rows += len(batch)
                batch = []

        if batch:
            _insert_batch(conn, batch)
            total_rows += len(batch)

        log_load(conn, "nass", source_file, len(parsed))
        logger.info("Loaded %d rows from %s", len(parsed), source_file)

    logger.info("Prospective Plantings load complete: %d total rows", total_rows)
    return total_rows


# ---------------------------------------------------------------------------
# Generic NASS loader helper
# ---------------------------------------------------------------------------

def _load_nass_report(conn, identifier, report_name, parser_fn, start_year=2010):
    """Generic loader: fetch releases, download .txt, parse, and insert."""
    logger.info("Fetching %s releases (from %d)...", report_name, start_year)
    releases = fetch_releases(identifier, start_year=start_year)
    logger.info("Found %d %s releases", len(releases), report_name)

    total_rows = 0
    table_type_cache = {}
    region_cache = {}
    unit_id_cache = {}

    for release_date, txt_url in releases:
        source_file = f"{identifier}-{release_date}"

        if already_loaded(conn, source_file):
            logger.info("Skipping %s (already loaded)", source_file)
            continue

        logger.info("Downloading %s ...", txt_url)
        try:
            text = download_report_txt(txt_url)
        except Exception:
            logger.warning("Failed to download %s, skipping", txt_url, exc_info=True)
            continue

        logger.info("Parsing %s ...", source_file)
        try:
            parsed = parser_fn(text, release_date)
        except Exception:
            logger.warning("Failed to parse %s, skipping", source_file, exc_info=True)
            continue

        if not parsed:
            logger.warning("No data parsed from %s", source_file)
            continue

        batch = []
        release_year = int(release_date[:4])
        release_month = int(release_date[5:7])

        for report_title, commodity, state_name, attribute, year_str, value, unit_str in parsed:
            tt_key = (report_title, commodity)
            if tt_key not in table_type_cache:
                table_type_cache[tt_key] = get_or_create_table_type(
                    conn, report_title, commodity, "U.S."
                )
            table_type_id = table_type_cache[tt_key]

            if state_name not in region_cache:
                region_cache[state_name] = get_or_create_region(conn, state_name)
            region_id = region_cache[state_name]

            if unit_str not in unit_id_cache:
                unit_id_cache[unit_str] = get_unit_id(conn, unit_str)
            unit_id = unit_id_cache[unit_str]

            batch.append((
                release_date, None, table_type_id, region_id,
                attribute, year_str, None, None,
                value, unit_str, unit_id, release_date, None,
                release_year, release_month,
            ))

            if len(batch) >= 5000:
                _insert_batch(conn, batch)
                total_rows += len(batch)
                batch = []

        if batch:
            _insert_batch(conn, batch)
            total_rows += len(batch)

        log_load(conn, "nass", source_file, len(parsed))
        logger.info("Loaded %d rows from %s", len(parsed), source_file)

    logger.info("%s load complete: %d total rows", report_name, total_rows)
    return total_rows


# ---------------------------------------------------------------------------
# Acreage Report
# ---------------------------------------------------------------------------

# Section header regex → (report_title, commodity, has_planted, has_harvested)
# Most sections have both planted and harvested (4 value cols).
# Some like Hay/Tobacco/Sugarcane only have harvested.
ACREAGE_SECTIONS = [
    (r"Corn Area Planted for All Purposes and Harvested for Grain",
     "Acreage", "Corn", True, True),
    (r"Sorghum Area Planted for All Purposes and Harvested for Grain",
     "Acreage", "Sorghum", True, True),
    (r"Oat Area Planted and Harvested",
     "Acreage", "Oats", True, True),
    (r"Barley Area Planted and Harvested",
     "Acreage", "Barley", True, True),
    (r"All Wheat Area Planted and Harvested",
     "Acreage", "All Wheat", True, True),
    (r"Winter Wheat Area Planted and Harvested",
     "Acreage", "Winter Wheat", True, True),
    (r"Durum Wheat Area Planted and Harvested",
     "Acreage", "Durum Wheat", True, True),
    (r"Other\s*\n?\s*Spring Wheat Area Planted and Harvested",
     "Acreage", "Other Spring Wheat", True, True),
    (r"Rye\s*\n?\s*Area Planted and Harvested",
     "Acreage", "Rye", True, True),
    (r"Soybean Area Planted and Harvested",
     "Acreage", "Soybeans", True, True),
    (r"Peanut Area Planted and Harvested",
     "Acreage", "Peanuts", True, True),
    (r"Canola\s*\n?\s*Area Planted and Harvested",
     "Acreage", "Canola", True, True),
    (r"Flaxseed Area Planted and Harvested",
     "Acreage", "Flaxseed", True, True),
    (r"Safflower Area Planted and Harvested",
     "Acreage", "Safflower", True, True),
    (r"Sugarbeet Area Planted\s*\n?\s*and Harvested",
     "Acreage", "Sugarbeets", True, True),
    (r"Dry\s*\n?\s*Edible Bean Area Planted and Harvested",
     "Acreage", "Dry Edible Beans", True, True),
    (r"Chickpea Area Planted and Harvested",
     "Acreage", "Chickpeas", True, True),
    (r"Lentil Area Planted and?\s*\n?\s*Harvested",
     "Acreage", "Lentils", True, True),
    (r"Dry Edible\s*\n?\s*Pea Area Planted and Harvested",
     "Acreage", "Dry Edible Peas", True, True),
    (r"Potato\s*\n?\s*Area Planted and Harvested",
     "Acreage", "Potatoes", True, True),
    (r"Proso Millet Area Planted and Harvested",
     "Acreage", "Proso Millet", True, True),
]

# Sub-type sections handled separately
ACREAGE_SUBTYPE_SECTIONS = [
    (r"Cotton Area Planted and Harvested by Type", "Acreage", "Cotton", True, True),
    (r"Sunflower Area Planted and Harvested by Type", "Acreage", "Sunflower", True, True),
    (r"Rice Area Planted\s*\n?\s*and Harvested by Class", "Acreage", "Rice", True, True),
]

ACREAGE_COTTON_SUBTYPES = {
    "Upland": "Upland Cotton",
    "American Pima": "American Pima Cotton",
    "All": "All Cotton",
}

ACREAGE_SUNFLOWER_SUBTYPES = {
    "Oil": "Oil Sunflower",
    "Non-oil": "Non-oil Sunflower",
    "All": "All Sunflower",
}

ACREAGE_RICE_SUBTYPES = {
    "Long grain": "Long Grain Rice",
    "Medium grain": "Medium Grain Rice",
    "Short grain": "Short Grain Rice",
    "All": "All Rice",
}

# Hay is special: 6 columns (All hay 2024/2025, Alfalfa 2024/2025, Other 2024/2025)
# all are "Area Harvested"
ACREAGE_HAY_PATTERN = r"Hay Area Harvested by Type"


def _find_acreage_section(line, next_line=""):
    """Check if a line starts an Acreage section. Returns config tuple or None."""
    if "......" in line:
        return None
    combined = line + "\n" + next_line
    for cfg in ACREAGE_SECTIONS:
        pattern = cfg[0]
        if re.search(pattern, line, re.IGNORECASE):
            return cfg
        if "......" not in next_line and re.search(pattern, combined, re.IGNORECASE):
            return cfg
    for cfg in ACREAGE_SUBTYPE_SECTIONS:
        pattern = cfg[0]
        if re.search(pattern, line, re.IGNORECASE):
            return cfg
        if "......" not in next_line and re.search(pattern, combined, re.IGNORECASE):
            return cfg
    if re.search(ACREAGE_HAY_PATTERN, line, re.IGNORECASE):
        return (ACREAGE_HAY_PATTERN, "Acreage", "Hay", False, True)
    return None


def _parse_acreage_data_rows(lines, start_idx, year_cols, has_planted, has_harvested,
                              report_title, commodity, unit_str,
                              subtype_map=None):
    """Parse state data rows from an Acreage report section.
    Returns (rows, end_idx).
    rows are (report_title, commodity, state, attribute, year_str, value, unit).
    """
    rows = []
    j = start_idx
    current_subtype = None

    while j < len(lines):
        raw = lines[j]
        stripped = raw.strip()

        # End of section
        if re.match(r'^\s*-{30,}', raw):
            j += 1
            break

        if not stripped:
            j += 1
            continue

        # Skip unit description lines
        if "1,000 acres" in stripped.lower() or "acres" == stripped.lower():
            j += 1
            continue

        # Sub-type detection
        if subtype_map:
            # Look for sub-type header like "Upland    :" or "Long grain    :" or just "Oil"
            # These have a label, optional colon, no significant numbers
            is_subtype_header = False
            for label, sub_commodity in subtype_map.items():
                if stripped.lower().startswith(label.lower()):
                    # Check it's not a state data row
                    rest = stripped[len(label):].strip()
                    if not rest or rest == ":" or (rest.startswith(":") and not re.search(r'\d', rest)):
                        # Also handle lines that are just the label (no colon)
                        if ":" not in stripped or not re.search(r'\d', stripped.split(":", 1)[1] if ":" in stripped else ""):
                            current_subtype = sub_commodity
                            is_subtype_header = True
                            break
            if is_subtype_header:
                j += 1
                continue

        # Must contain ":" for a data row
        if ":" not in raw:
            j += 1
            continue

        colon_idx = raw.index(":")
        state_part = raw[:colon_idx]
        values_part = raw[colon_idx + 1:]

        state_name = _clean_state_name(state_part)
        if not state_name:
            j += 1
            continue

        # Skip non-state header lines
        skip_names = {"state", "type and state", "class and state", "varietal type",
                      "class, type, and state", "size and state", "class and type"}
        if state_name.lower() in skip_names:
            j += 1
            continue

        # Parse numeric values
        tokens = values_part.split()
        numeric_tokens = []
        for tok in tokens:
            cleaned = re.sub(r'\s*\d+/$', '', tok.strip())
            if cleaned in ("", "(X)", "(NA)", "(D)", "(Z)"):
                numeric_tokens.append(cleaned)
            elif cleaned == "-":
                numeric_tokens.append(cleaned)
            else:
                try:
                    float(cleaned.replace(",", ""))
                    numeric_tokens.append(tok)
                except ValueError:
                    continue

        effective_commodity = current_subtype if current_subtype else commodity

        # Determine expected column count based on has_planted/has_harvested
        if has_planted and has_harvested:
            # 4 columns: planted_y1, planted_y2, harvested_y1, harvested_y2
            num_years = len(year_cols) // 2 if len(year_cols) >= 4 else len(year_cols)
            if len(numeric_tokens) >= 4 and len(year_cols) >= 4:
                for k in range(min(2, num_years)):
                    val = _parse_value(numeric_tokens[k])
                    rows.append((report_title, effective_commodity, state_name,
                                 "Area Planted", str(year_cols[k]), val, unit_str))
                for k in range(min(2, num_years)):
                    idx = k + 2
                    if idx < len(numeric_tokens):
                        val = _parse_value(numeric_tokens[idx])
                        rows.append((report_title, effective_commodity, state_name,
                                     "Area Harvested", str(year_cols[idx]), val, unit_str))
            elif len(numeric_tokens) >= 2:
                # Fewer columns than expected, treat as planted only
                for k, val_str in enumerate(numeric_tokens[:len(year_cols)]):
                    val = _parse_value(val_str)
                    rows.append((report_title, effective_commodity, state_name,
                                 "Area Planted", str(year_cols[k]), val, unit_str))
        elif has_harvested:
            # Harvested only (e.g., Hay)
            for k, val_str in enumerate(numeric_tokens):
                if k >= len(year_cols):
                    break
                val = _parse_value(val_str)
                rows.append((report_title, effective_commodity, state_name,
                             "Area Harvested", str(year_cols[k]), val, unit_str))
        else:
            # Planted only
            for k, val_str in enumerate(numeric_tokens):
                if k >= len(year_cols):
                    break
                val = _parse_value(val_str)
                rows.append((report_title, effective_commodity, state_name,
                             "Area Planted", str(year_cols[k]), val, unit_str))

        j += 1

    return rows, j


def parse_acreage_txt(text, release_date):
    """Parse an Acreage .txt report into structured rows.
    Returns list of (report_title, commodity, state, attribute, market_year_str, value, unit).
    """
    lines = text.split("\n")
    rows = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""

        section = _find_acreage_section(line, next_line)
        if section is None:
            i += 1
            continue

        _pattern, report_title, commodity, has_planted, has_harvested = section

        # Determine sub-type map
        subtype_map = None
        if commodity == "Cotton":
            subtype_map = ACREAGE_COTTON_SUBTYPES
        elif commodity == "Sunflower":
            subtype_map = ACREAGE_SUNFLOWER_SUBTYPES
        elif commodity == "Rice":
            subtype_map = ACREAGE_RICE_SUBTYPES

        # Find year columns — prefer the line with the most year values
        # (column header line has 4 years for planted+harvested sections)
        year_cols = []
        j = i + 1
        best_j = j
        while j < min(i + 30, len(lines)):
            years = _extract_year_columns(lines[j])
            if len(years) > len(year_cols):
                year_cols = years
                best_j = j
            # If we already found 4+ years, we're done
            if len(year_cols) >= 4:
                break
            j += 1
        j = best_j

        if not year_cols:
            i += 1
            continue

        # Skip to data rows past the dashed separator
        dash_count = 0
        j += 1
        while j < min(i + 50, len(lines)):
            if re.match(r'^\s*-{20,}\s*$', lines[j].strip()):
                dash_count += 1
                if dash_count >= 1:
                    j += 1
                    break
            j += 1

        # Skip unit description lines
        while j < min(i + 60, len(lines)):
            stripped_j = lines[j].strip()
            if not stripped_j:
                j += 1
                continue
            if "1,000 acres" in stripped_j.lower():
                j += 1
                continue
            if re.match(r'^\s+:', stripped_j):
                j += 1
                continue
            break

        # For Hay, the year_cols have pairs: all_2024, all_2025, alf_2024, alf_2025, other_2024, other_2025
        # But we only have 2 unique years. Hay is special - skip for now and parse as regular harvested.
        # Simplification: just use the first 2 years found for standard sections.

        section_rows, end_j = _parse_acreage_data_rows(
            lines, j, year_cols, has_planted, has_harvested,
            report_title, commodity, UNIT_ACRES,
            subtype_map=subtype_map,
        )
        rows.extend(section_rows)
        i = end_j if end_j > i else i + 1

    return rows


def load_acreage_reports(conn, start_year=2010):
    """Load NASS Acreage data from the ESMIS API into SQLite."""
    return _load_nass_report(conn, "Acre", "Acreage", parse_acreage_txt, start_year)


# ---------------------------------------------------------------------------
# Grain Stocks Report
# ---------------------------------------------------------------------------

GRAIN_STOCK_SECTIONS = [
    (r"Corn Stocks by Position\b", "Grain Stocks", "Corn", "1,000 Bushels"),
    (r"Sorghum Stocks by Position\b", "Grain Stocks", "Sorghum", "1,000 Bushels"),
    (r"Oat Stocks by Position\b", "Grain Stocks", "Oats", "1,000 Bushels"),
    (r"Barley Stocks by Position\b", "Grain Stocks", "Barley", "1,000 Bushels"),
    (r"All Wheat Stocks by Position\b", "Grain Stocks", "All Wheat", "1,000 Bushels"),
    (r"Durum Wheat Stocks by Position\b", "Grain Stocks", "Durum Wheat", "1,000 Bushels"),
    (r"Soybean\s*\n?\s*Stocks by Position\b", "Grain Stocks", "Soybeans", "1,000 Bushels"),
    (r"Sunflower Stocks by Position\b", "Grain Stocks", "Sunflower", "1,000 Pounds"),
]

# Combined tables: "Corn and Sorghum Stocks", "Oat and Barley Stocks", "All Wheat and Soybean Stocks"
GRAIN_STOCK_COMBINED = [
    (r"Corn and Sorghum Stocks by Position\b",
     [("Grain Stocks", "Corn", "1,000 Bushels"), ("Grain Stocks", "Sorghum", "1,000 Bushels")]),
    (r"Oat and Barley Stocks by Position\b",
     [("Grain Stocks", "Oats", "1,000 Bushels"), ("Grain Stocks", "Barley", "1,000 Bushels")]),
    (r"All Wheat and Soybean Stocks by Position\b",
     [("Grain Stocks", "All Wheat", "1,000 Bushels"), ("Grain Stocks", "Soybeans", "1,000 Bushels")]),
]


def _extract_stock_dates(lines, start, end):
    """Extract dates like 'March 1, 2025 and 2026' from header lines."""
    header_text = " ".join(lines[start:end])
    # Look for "Month Day, Year and Year" or "Month Day, Year"
    m = re.search(r'(\w+ \d+),?\s+(\d{4})\s+and\s+(\d{4})', header_text)
    if m:
        return [f"{m.group(1)}, {m.group(2)}", f"{m.group(1)}, {m.group(3)}"]
    m = re.search(r'(\w+ \d+),?\s+(\d{4})', header_text)
    if m:
        return [f"{m.group(1)}, {m.group(2)}"]
    return []


def parse_grain_stocks_txt(text, release_date):
    """Parse a Grain Stocks .txt report into structured rows.
    Returns list of (report_title, commodity, state, attribute, market_year_str, value, unit).
    """
    lines = text.split("\n")
    rows = []
    i = 0
    seen_sections = set()

    while i < len(lines):
        line = lines[i].strip()
        if "......" in line:
            i += 1
            continue

        # Check for single-commodity sections
        matched_section = None
        for pattern, report_title, commodity, unit_str in GRAIN_STOCK_SECTIONS:
            if re.search(pattern, line, re.IGNORECASE):
                matched_section = [(report_title, commodity, unit_str)]
                break

        # Check for combined sections
        if not matched_section:
            for pattern, commodities in GRAIN_STOCK_COMBINED:
                if re.search(pattern, line, re.IGNORECASE):
                    matched_section = commodities
                    break

        if not matched_section:
            i += 1
            continue

        # Extract stock dates from surrounding header lines
        stock_dates = _extract_stock_dates(lines, max(0, i - 2), min(len(lines), i + 5))
        if not stock_dates:
            i += 1
            continue

        # Create a dedup key to avoid double-parsing
        section_key = (matched_section[0][1], tuple(stock_dates))
        if section_key in seen_sections:
            i += 1
            continue
        seen_sections.add(section_key)

        is_combined = len(matched_section) > 1

        # Find the LAST dashed separator before data rows begin
        # (grain stocks tables have 2 separators: above and below column headers)
        j = i + 1
        last_sep = j
        while j < min(i + 30, len(lines)):
            if re.match(r'^\s*-{20,}\s*$', lines[j].strip()):
                last_sep = j
            # Stop scanning after we see a data line (state with colon and numbers)
            stripped_j = lines[j].strip()
            if "......" in stripped_j and ":" in stripped_j:
                break
            j += 1
        # Start from just past the last separator
        j = last_sep + 1
        # Skip unit description / blank lines
        while j < min(i + 50, len(lines)):
            stripped_j = lines[j].strip()
            if not stripped_j or "1,000 bushels" in stripped_j.lower() or "1,000 pounds" in stripped_j.lower():
                j += 1
                continue
            if stripped_j.startswith(":") and not re.search(r'\.\.\.\.\.\.\.*:', stripped_j):
                j += 1
                continue
            break

        # Parse state rows
        # Single commodity: 6 cols = on_y1, off_y1, total_y1, on_y2, off_y2, total_y2
        # Combined: 6 cols = crop1_on, crop1_off, crop1_total, crop2_on, crop2_off, crop2_total
        # (Only single date in combined tables)

        while j < len(lines):
            raw = lines[j]
            stripped = raw.strip()

            if re.match(r'^\s*-{30,}', raw):
                j += 1
                break
            if not stripped:
                j += 1
                continue

            if ":" not in raw:
                j += 1
                continue

            colon_idx = raw.index(":")
            state_part = raw[:colon_idx]
            values_part = raw[colon_idx + 1:]

            state_name = _clean_state_name(state_part)
            if not state_name:
                j += 1
                continue

            skip_names = {"state", "date", "varietal type", "type and state"}
            if state_name.lower() in skip_names:
                j += 1
                continue

            # Skip "Unallocated" rows
            if "unallocated" in state_name.lower():
                j += 1
                continue

            tokens = values_part.split()
            numeric_tokens = []
            for tok in tokens:
                cleaned = re.sub(r'\s*\d+/$', '', tok.strip())
                if cleaned in ("", "(X)", "(NA)", "(D)", "(Z)"):
                    numeric_tokens.append(cleaned)
                elif cleaned == "-":
                    numeric_tokens.append(cleaned)
                else:
                    try:
                        float(cleaned.replace(",", ""))
                        numeric_tokens.append(tok)
                    except ValueError:
                        continue

            # Attributes: On Farms, Off Farms, Total
            attributes = ["On Farms", "Off Farms", "Total"]

            if is_combined:
                # 6 cols: crop1_on, crop1_off, crop1_total, crop2_on, crop2_off, crop2_total
                # Only 1 date in combined tables (e.g., December 1, 2025)
                date_str = stock_dates[0] if stock_dates else release_date
                for c_idx, (rt, comm, unit) in enumerate(matched_section):
                    base = c_idx * 3
                    for a_idx, attr in enumerate(attributes):
                        tok_idx = base + a_idx
                        if tok_idx < len(numeric_tokens):
                            val = _parse_value(numeric_tokens[tok_idx])
                            rows.append((rt, comm, state_name, attr, date_str, val, unit))
            else:
                # 6 cols: on_y1, off_y1, total_y1, on_y2, off_y2, total_y2
                rt, comm, unit = matched_section[0]
                for d_idx, date_str in enumerate(stock_dates):
                    base = d_idx * 3
                    for a_idx, attr in enumerate(attributes):
                        tok_idx = base + a_idx
                        if tok_idx < len(numeric_tokens):
                            val = _parse_value(numeric_tokens[tok_idx])
                            rows.append((rt, comm, state_name, attr, date_str, val, unit))

            j += 1

        i = j if j > i else i + 1

    return rows


def load_grain_stocks(conn, start_year=2010):
    """Load NASS Grain Stocks data from the ESMIS API into SQLite."""
    return _load_nass_report(conn, "GraiStoc", "Grain Stocks", parse_grain_stocks_txt, start_year)


# ---------------------------------------------------------------------------
# Crop Progress Report
# ---------------------------------------------------------------------------

# Progress sections: (header_regex, commodity, attribute_prefix)
CROP_PROGRESS_SECTIONS = [
    (r"^Corn Planted\b", "Corn", "Planted"),
    (r"^Corn Emerged\b", "Corn", "Emerged"),
    (r"^Corn Dough\b", "Corn", "Dough"),
    (r"^Corn Dented\b", "Corn", "Dented"),
    (r"^Corn Mature\b", "Corn", "Mature"),
    (r"^Corn Harvested\b", "Corn", "Harvested"),
    (r"^Cotton Planted\b", "Cotton", "Planted"),
    (r"^Cotton Squaring\b", "Cotton", "Squaring"),
    (r"^Cotton Setting Bolls\b", "Cotton", "Setting Bolls"),
    (r"^Cotton Bolls Opening\b", "Cotton", "Bolls Opening"),
    (r"^Cotton Harvested\b", "Cotton", "Harvested"),
    (r"^Sorghum Planted\b", "Sorghum", "Planted"),
    (r"^Sorghum Headed\b", "Sorghum", "Headed"),
    (r"^Sorghum Coloring\b", "Sorghum", "Coloring"),
    (r"^Sorghum Mature\b", "Sorghum", "Mature"),
    (r"^Sorghum Harvested\b", "Sorghum", "Harvested"),
    (r"^Rice Planted\b", "Rice", "Planted"),
    (r"^Rice Emerged\b", "Rice", "Emerged"),
    (r"^Rice Headed\b", "Rice", "Headed"),
    (r"^Rice Harvested\b", "Rice", "Harvested"),
    (r"^Sugarbeets? Planted\b", "Sugarbeets", "Planted"),
    (r"^Sugarbeets? Harvested\b", "Sugarbeets", "Harvested"),
    (r"^Oats? Planted\b", "Oats", "Planted"),
    (r"^Oats? Emerged\b", "Oats", "Emerged"),
    (r"^Oats? Headed\b", "Oats", "Headed"),
    (r"^Oats? Harvested\b", "Oats", "Harvested"),
    (r"^Spring Wheat Planted\b", "Spring Wheat", "Planted"),
    (r"^Spring Wheat Emerged\b", "Spring Wheat", "Emerged"),
    (r"^Spring Wheat Headed\b", "Spring Wheat", "Headed"),
    (r"^Spring Wheat Harvested\b", "Spring Wheat", "Harvested"),
    (r"^Barley Planted\b", "Barley", "Planted"),
    (r"^Barley Emerged\b", "Barley", "Emerged"),
    (r"^Barley Headed\b", "Barley", "Headed"),
    (r"^Barley Harvested\b", "Barley", "Harvested"),
    (r"^Soybeans? Planted\b", "Soybeans", "Planted"),
    (r"^Soybeans? Emerged\b", "Soybeans", "Emerged"),
    (r"^Soybeans? Blooming\b", "Soybeans", "Blooming"),
    (r"^Soybeans? Setting Pods\b", "Soybeans", "Setting Pods"),
    (r"^Soybeans? Dropping Leaves\b", "Soybeans", "Dropping Leaves"),
    (r"^Soybeans? Harvested\b", "Soybeans", "Harvested"),
    (r"^Peanuts? Planted\b", "Peanuts", "Planted"),
    (r"^Sunflower Planted\b", "Sunflower", "Planted"),
    (r"^Winter Wheat Headed\b", "Winter Wheat", "Headed"),
    (r"^Winter Wheat Harvested\b", "Winter Wheat", "Harvested"),
]

# Condition sections: 5 columns (Very Poor, Poor, Fair, Good, Excellent)
CROP_CONDITION_SECTIONS = [
    (r"^Winter Wheat Condition\b", "Winter Wheat"),
    (r"^Corn Condition\b", "Corn"),
    (r"^Soybean Condition\b", "Soybeans"),
    (r"^Cotton Condition\b", "Cotton"),
    (r"^Sorghum Condition\b", "Sorghum"),
    (r"^Spring Wheat Condition\b", "Spring Wheat"),
    (r"^Barley Condition\b", "Barley"),
    (r"^Rice Condition\b", "Rice"),
    (r"^Pasture and Range Condition\b", "Pasture and Range"),
]

CONDITION_ATTRS = ["Very Poor", "Poor", "Fair", "Good", "Excellent"]

# Moisture sections: 4 columns (Very Short, Short, Adequate, Surplus)
MOISTURE_SECTIONS = [
    (r"^Topsoil Moisture Condition\b", "Topsoil Moisture"),
    (r"^Subsoil Moisture Condition\b", "Subsoil Moisture"),
]

MOISTURE_ATTRS = ["Very Short", "Short", "Adequate", "Surplus"]


def _extract_week_ending(lines, start, end):
    """Extract the current week-ending date from Crop Progress column headers.
    Returns a date string like '2026-04-05' for the current week column (3rd col).
    """
    header_text = " ".join(l.strip() for l in lines[start:end])
    # Look for the section title containing "Week Ending Month Day, Year"
    m = re.search(r'Week Ending\s+(\w+\s+\d+),?\s*(\d{4})', header_text, re.IGNORECASE)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%B %d %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _extract_progress_current_date(lines, header_idx):
    """From a progress table's column headers, extract the current-week date.
    Column order: prev_year_same_week, prev_week, current_week, 5yr_avg
    The date columns look like:  'April 5,'  'March 29,'  'April 5,'
                                 '  2025    '  '  2026    '  '  2026    '
    We want the 3rd date (current week).
    """
    # Scan forward to find date header lines
    for j in range(header_idx + 1, min(header_idx + 15, len(lines))):
        line = lines[j]
        # Look for year line with multiple years
        year_matches = re.findall(r'(\d{4})', line)
        if len(year_matches) >= 2:
            # The line above should have month/day references
            prev_line = lines[j - 1] if j > 0 else ""
            # Extract month+day pairs
            date_parts = re.findall(r'(\w+)\s+(\d+)', prev_line)
            if len(date_parts) >= 3 and len(year_matches) >= 3:
                month_str, day_str = date_parts[2]  # 3rd date = current week
                year_str = year_matches[2]
                try:
                    dt = datetime.strptime(f"{month_str} {day_str} {year_str}", "%B %d %Y")
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
    return None


def _skip_to_data_rows(lines, start, max_scan=30, unit_keywords=None):
    """Find the last dashed separator in the header block and advance past it,
    then skip unit/blank lines. Returns the index of the first data row."""
    if unit_keywords is None:
        unit_keywords = ["percent", "days"]
    last_sep = start
    j = start
    while j < min(start + max_scan, len(lines)):
        stripped = lines[j].strip()
        if re.match(r'^\s*-{20,}\s*$', stripped):
            last_sep = j
        # If we see a line with dots and colon (data row), stop scanning
        if "......" in stripped and ":" in stripped:
            break
        j += 1
    j = last_sep + 1
    while j < min(start + max_scan + 10, len(lines)):
        s = lines[j].strip()
        if not s:
            j += 1
            continue
        if any(kw in s.lower() for kw in unit_keywords):
            j += 1
            continue
        if s.startswith(":") and not re.search(r'\.\.\.\.', s):
            j += 1
            continue
        break
    return j


def parse_crop_progress_txt(text, release_date):
    """Parse a Crop Progress .txt report into structured rows.
    Returns list of (report_title, commodity, state, attribute, market_year_str, value, unit).
    Only stores the current week's values.
    """
    lines = text.split("\n")
    rows = []
    i = 0
    report_title = "Crop Progress"

    # Derive crop year from release date
    release_dt = datetime.strptime(release_date, "%Y-%m-%d")
    crop_year = str(release_dt.year) if release_dt.month >= 4 else str(release_dt.year - 1)

    while i < len(lines):
        line = lines[i].strip()
        if "......" in line:
            i += 1
            continue

        # Check progress sections
        progress_match = None
        for pattern, commodity, attr_prefix in CROP_PROGRESS_SECTIONS:
            if re.search(pattern, line, re.IGNORECASE) and "Selected States" in line:
                progress_match = (commodity, attr_prefix)
                break
            # Handle when "Selected States" is on next line
            next_l = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if re.search(pattern, line, re.IGNORECASE) and "Selected States" in next_l:
                progress_match = (commodity, attr_prefix)
                break

        if progress_match:
            commodity, attr_prefix = progress_match
            # Find the current week date from column headers
            current_date = _extract_progress_current_date(lines, i)
            market_year = current_date or crop_year

            # Skip past column headers to data rows
            j = _skip_to_data_rows(lines, i + 1)

            while j < len(lines):
                raw = lines[j]
                stripped = raw.strip()
                if re.match(r'^\s*-{30,}', raw):
                    j += 1
                    break
                if not stripped:
                    j += 1
                    continue
                if ":" not in raw:
                    j += 1
                    continue

                colon_idx = raw.index(":")
                state_part = raw[:colon_idx]
                values_part = raw[colon_idx + 1:]
                state_name = _clean_state_name(state_part)
                if not state_name or state_name.lower() in ("state",):
                    j += 1
                    continue

                # Normalize "N States" summary to a standard name
                if re.match(r'^\d+ States', state_name):
                    state_name = "United States"

                tokens = values_part.split()
                numeric_tokens = []
                for tok in tokens:
                    cleaned = re.sub(r'\s*\d+/$', '', tok.strip())
                    if cleaned in ("", "(NA)", "(X)"):
                        numeric_tokens.append(cleaned)
                    elif cleaned == "-":
                        numeric_tokens.append(cleaned)
                    else:
                        try:
                            float(cleaned.replace(",", ""))
                            numeric_tokens.append(tok)
                        except ValueError:
                            continue

                # 4 columns: prev_year, prev_week, current_week, 5yr_avg
                # Store only current_week (index 2)
                if len(numeric_tokens) >= 3:
                    val = _parse_value(numeric_tokens[2])
                    rows.append((report_title, commodity, state_name,
                                 attr_prefix, market_year, val, "Percent"))

                j += 1
            i = j if j > i else i + 1
            continue

        # Check condition sections
        condition_match = None
        for pattern, commodity in CROP_CONDITION_SECTIONS:
            if re.search(pattern, line, re.IGNORECASE):
                condition_match = commodity
                break

        if condition_match:
            # Extract week ending date from header
            week_date = _extract_week_ending(lines, max(0, i - 2), min(len(lines), i + 5))
            market_year = week_date or crop_year

            j = _skip_to_data_rows(lines, i + 1)

            while j < len(lines):
                raw = lines[j]
                stripped = raw.strip()
                if re.match(r'^\s*-{30,}', raw):
                    j += 1
                    break
                if not stripped:
                    j += 1
                    continue
                # Skip "Previous week" / "Previous year" rows
                if "previous" in stripped.lower():
                    j += 1
                    continue
                if ":" not in raw:
                    j += 1
                    continue

                colon_idx = raw.index(":")
                state_part = raw[:colon_idx]
                values_part = raw[colon_idx + 1:]
                state_name = _clean_state_name(state_part)
                if not state_name or state_name.lower() in ("state",):
                    j += 1
                    continue
                if re.match(r'^\d+ States', state_name):
                    state_name = "United States"

                tokens = values_part.split()
                numeric_tokens = []
                for tok in tokens:
                    cleaned = re.sub(r'\s*\d+/$', '', tok.strip())
                    if cleaned in ("", "(NA)", "(X)"):
                        numeric_tokens.append(cleaned)
                    elif cleaned == "-":
                        numeric_tokens.append(cleaned)
                    else:
                        try:
                            float(cleaned.replace(",", ""))
                            numeric_tokens.append(tok)
                        except ValueError:
                            continue

                for a_idx, attr in enumerate(CONDITION_ATTRS):
                    if a_idx < len(numeric_tokens):
                        val = _parse_value(numeric_tokens[a_idx])
                        rows.append((report_title, condition_match, state_name,
                                     f"Condition {attr}", market_year, val, "Percent"))

                j += 1
            i = j if j > i else i + 1
            continue

        # Check moisture sections
        moisture_match = None
        for pattern, moisture_type in MOISTURE_SECTIONS:
            if re.search(pattern, line, re.IGNORECASE):
                moisture_match = moisture_type
                break

        if moisture_match:
            week_date = _extract_week_ending(lines, max(0, i - 2), min(len(lines), i + 5))
            market_year = week_date or crop_year

            j = _skip_to_data_rows(lines, i + 1)

            while j < len(lines):
                raw = lines[j]
                stripped = raw.strip()
                if re.match(r'^\s*-{30,}', raw):
                    j += 1
                    break
                if not stripped:
                    j += 1
                    continue
                if "previous" in stripped.lower():
                    j += 1
                    continue
                if ":" not in raw:
                    j += 1
                    continue

                colon_idx = raw.index(":")
                state_part = raw[:colon_idx]
                values_part = raw[colon_idx + 1:]
                state_name = _clean_state_name(state_part)
                if not state_name or state_name.lower() in ("state",):
                    j += 1
                    continue
                if re.match(r'^\d+ States', state_name):
                    state_name = "United States"

                tokens = values_part.split()
                numeric_tokens = []
                for tok in tokens:
                    cleaned = re.sub(r'\s*\d+/$', '', tok.strip())
                    if cleaned in ("", "(NA)", "(X)"):
                        numeric_tokens.append(cleaned)
                    elif cleaned == "-":
                        numeric_tokens.append(cleaned)
                    else:
                        try:
                            float(cleaned.replace(",", ""))
                            numeric_tokens.append(tok)
                        except ValueError:
                            continue

                for a_idx, attr in enumerate(MOISTURE_ATTRS):
                    if a_idx < len(numeric_tokens):
                        val = _parse_value(numeric_tokens[a_idx])
                        rows.append(("Crop Progress", moisture_match, state_name,
                                     attr, market_year, val, "Percent"))

                j += 1
            i = j if j > i else i + 1
            continue

        # Check Days Suitable for Fieldwork
        if re.search(r"^Days Suitable for Fieldwork", line, re.IGNORECASE):
            # 3 columns: prev_year, prev_week, current_week
            current_date = _extract_progress_current_date(lines, i)
            market_year = current_date or crop_year

            j = _skip_to_data_rows(lines, i + 1, unit_keywords=["days"])

            while j < len(lines):
                raw = lines[j]
                stripped = raw.strip()
                if re.match(r'^\s*-{20,}', raw):
                    j += 1
                    break
                if not stripped:
                    j += 1
                    continue
                if ":" not in raw:
                    j += 1
                    continue

                colon_idx = raw.index(":")
                state_part = raw[:colon_idx]
                values_part = raw[colon_idx + 1:]
                state_name = _clean_state_name(state_part)
                if not state_name or state_name.lower() in ("state",):
                    j += 1
                    continue

                tokens = values_part.split()
                numeric_tokens = []
                for tok in tokens:
                    cleaned = re.sub(r'\s*\d+/$', '', tok.strip())
                    if cleaned in ("", "(NA)", "(X)"):
                        numeric_tokens.append(cleaned)
                    elif cleaned == "-":
                        numeric_tokens.append(cleaned)
                    else:
                        try:
                            float(cleaned.replace(",", ""))
                            numeric_tokens.append(tok)
                        except ValueError:
                            continue

                # 3 columns: prev_year, prev_week, current_week — store current (index 2)
                if len(numeric_tokens) >= 3:
                    val = _parse_value(numeric_tokens[2])
                    rows.append(("Crop Progress", "Fieldwork", state_name,
                                 "Days Suitable", market_year, val, "Days"))

                j += 1
            i = j if j > i else i + 1
            continue

        i += 1

    return rows


def load_crop_progress(conn, start_year=2010):
    """Load NASS Crop Progress data from the ESMIS API into SQLite."""
    return _load_nass_report(conn, "CropProg", "Crop Progress", parse_crop_progress_txt, start_year)
