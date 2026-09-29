# WASDE Data Puller

Downloads USDA WASDE (World Agricultural Supply and Demand Estimates) CSV reports and loads them into a normalized SQLite database. Also includes NASS Prospective Plantings data and support for rough/preliminary TXT reports.

## Prerequisites

- Python 3.9+
- `bash` and `curl` (for CSV downloads)

## Setup

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Full Database Rebuild

The simplest way to build (or rebuild) the entire database from scratch:

```bash
python -m src.main --rebuild
```

This will:
1. Download all missing WASDE CSV files from USDA (2011 through the current month)
2. Delete the existing `wasde.db` if present
3. Create the SQLite schema (tables, views, unit seed data)
4. Load all CSV files into the database
5. Load NASS Prospective Plantings data (2010–present)

The database is created at `wasde.db` in the project root.

## Individual Steps

### Download CSVs only

```bash
# Via the Python CLI
python -m src.main --download

# Or run the bash script directly
bash scripts/download_csvs.sh
```

Downloads any missing monthly CSV files from `https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-{YYYY}-{MM}.csv`. Already-downloaded files are skipped.

### Load CSVs into database

```bash
python -m src.main --load-csv
```

Loads all CSV files from `data/csv/` into the database. Files already loaded (tracked in `load_log`) are skipped.

### Load NASS Prospective Plantings

```bash
python -m src.main --nass
python -m src.main --nass --nass-start-year 2020  # limit to recent years
```

Fetches crop acreage data (corn, soybeans, wheat, cotton) from the ESMIS API.

### Rough (preliminary) TXT reports

Load a preliminary WASDE report before the official CSV is published:

```bash
# Load rough report for current month
python -m src.main --rough

# Load rough report for a specific month
python -m src.main --rough --txt-month 4 --txt-year 2026

# Load from a local file
python -m src.main --rough --txt-file data/txt/wasde0426.txt

# Later, promote rough data when the final report is available
python -m src.main --promote-rough --txt-month 4 --txt-year 2026
```

Rough data goes into `wasde_data_rough` and is compared with final data via the `v_rough_vs_final` view.

## CLI Reference

| Flag | Description |
|------|-------------|
| `--rebuild` | Full rebuild: download + recreate DB + load CSV + load NASS |
| `--download` | Download missing CSV files from USDA |
| `--load-csv` | Load CSV files from `data/csv/` into the database |
| `--nass` | Load NASS Prospective Plantings data |
| `--nass-start-year N` | Start year for NASS data (default: 2010) |
| `--rough` | Load rough TXT report into `wasde_data_rough` |
| `--promote-rough` | Load finalized TXT into `wasde_data`, keep rough for comparison |
| `--txt-file PATH` | Use a local `.txt` file instead of downloading |
| `--txt-month M` | Month for TXT report (default: current) |
| `--txt-year Y` | Year for TXT report (default: current) |
| `--db PATH` | SQLite database path (default: `wasde.db`) |

## Verification

```bash
python scripts/verify.py
```

Prints row counts, date ranges, table types, and sample data from the database.

## Database Schema

### Tables

| Table | Description |
|-------|-------------|
| `wasde_data` | Main fact table with all WASDE data points |
| `wasde_data_rough` | Preliminary/rough report data |
| `table_types` | Report title lookup (e.g. "U.S. Wheat Supply and Use") |
| `regions` | Region/country lookup with optional parent hierarchy |
| `units` | Unit definitions with conversion multipliers |
| `load_log` | Tracks which source files have been loaded |

### Views

| View | Description |
|------|-------------|
| `v_wasde` | Denormalized view joining all lookup tables |
| `v_latest_estimates` | Most recent estimate for each data point |
| `v_month_over_month` | Month-over-month changes and percent changes |
| `v_supply_use_balance` | Pivoted supply/use balance sheet |
| `v_revision_history` | Revision deltas across report months |
| `v_rough_vs_final` | Comparison of rough vs. finalized data |
| `v_world_with_hierarchy` | Region hierarchy tree |

## Data Sources

- **WASDE CSVs**: `https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-{YYYY}-{MM}.csv`
- **WASDE TXT** (rough reports): `https://www.usda.gov/oce/commodity/wasde/wasde{MMYY}.txt`
- **NASS Prospective Plantings**: ESMIS API (`https://esmis.nal.usda.gov/api/v1`)
