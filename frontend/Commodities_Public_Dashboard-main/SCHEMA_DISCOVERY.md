# WASDE Database Schema Discovery

## Database: `backend/grady-data-puller-main/data/wasde.db` (SQLite)

The counts below are a snapshot of the local WASDE CSV pull on September 29, 2026. They will change when more reports or NASS data are loaded. The database is not committed to this repository.

---

## Tables

| Table | Rows | Description |
|-------|------|-------------|
| `wasde_data` | 232,006 | Primary fact table — final WASDE report data |
| `wasde_data_rough` | 0 | Rough/preliminary data (currently empty) |
| `table_types` | 71 | Report title + commodity + scope lookup |
| `regions` | 44 | Self-referential region hierarchy (parent_id) |
| `units` | 34 | Unit conversion table (category, base_unit, multiplier) |
| `load_log` | 66 | ETL load audit log |

## Views

| View | Purpose |
|------|---------|
| **`v_wasde`** | Main denormalized view — joins wasde_data + table_types + regions + units. Unions final & rough data (rough only where final missing). |
| `v_latest_estimates` | Most recent report_date per (table_type, region, attribute, market_year) |
| `v_month_over_month` | Current vs previous report values with change & % change |
| `v_revision_history` | Value changes over time with LAG window function |
| `v_rough_vs_final` | Side-by-side rough vs final values |
| `v_supply_use_balance` | Pivot of supply/use attributes into columns (beginning_stocks, production, etc.) |
| `v_world_with_hierarchy` | Recursive CTE for region hierarchy tree |

---

## `v_wasde` View — Column Reference

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Row ID from wasde_data |
| `report_date` | TEXT | Report publication date (YYYY-MM-DD) |
| `wasde_number` | INTEGER | WASDE report number (NULL for non-WASDE reports) |
| `market_year` | TEXT | Marketing year (e.g. "2024/25" or "2024") |
| `attribute` | TEXT | Report value name — the measure being reported (81 distinct in this snapshot) |
| `value` | REAL | Raw numeric value |
| `unit` | TEXT | Display unit string (e.g. "1,000 Acres", "Million Bushels") |
| `proj_est_flag` | TEXT | Projection/estimate flag |
| `annual_quarter_flag` | TEXT | Annual vs quarterly flag |
| `forecast_year` | INTEGER | Year of the forecast |
| `forecast_month` | INTEGER | Month of the forecast |
| `report_title` | TEXT | Report name (from table_types) |
| `commodity` | TEXT | Commodity name (28 distinct in this snapshot) |
| `scope` | TEXT | `U.S.`, `World`, or `None` |
| `region` | TEXT | Country/aggregate name (44 distinct in this snapshot) |
| `parent_region` | TEXT | Parent region name (from self-join on regions) |
| `unit_category` | TEXT | Unit category (area, weight, volume, etc.) |
| `base_unit` | TEXT | Canonical unit after conversion |
| `unit_multiplier` | REAL | Multiplier to convert value → base_value |
| `base_value` | REAL | `value * unit_multiplier` (NULL if no multiplier) |
| `data_source` | TEXT | `'final'` or `'rough'` |

---

## Data Dimensions

### Date Range
- **2021-01-01** to **2026-09-01** (66 distinct report dates). October 2025 and May–June 2026 were unavailable during the first pull.

### Scopes
- `U.S.` — domestic reports
- `World` — global supply/demand
- `None` — unclassified

### Commodities (28 in this snapshot)
Examples include Corn, Wheat, Rice, Soybean Meal, Soybean Oil, Cotton, Sugar, Beef, Milk, Total Grains, and Oilseeds. Additional commodities can appear when other source families are loaded.

### Key attributes (81 in this snapshot)
Examples include Area Planted, Area Harvested, Yield, Production, Beginning Stocks, Imports, Feed and Residual, Exports, Domestic Use, Ending Stocks, Stocks to Use Ratio, and Avg. Farm Price.

### Regions (44 in this snapshot)
Examples include United States, China, Brazil, Argentina, India, Australia, Russia, World, Total Foreign, Major Exporter, and World Less China. The first CSV-only pull does not contain all U.S. states.

---

## Base Table DDLs

```sql
CREATE TABLE wasde_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    wasde_number INTEGER,
    table_type_id INTEGER NOT NULL REFERENCES table_types(id),
    region_id INTEGER NOT NULL REFERENCES regions(id),
    attribute TEXT NOT NULL,
    market_year TEXT NOT NULL,
    proj_est_flag TEXT,
    annual_quarter_flag TEXT,
    value REAL,
    unit TEXT,
    unit_id INTEGER REFERENCES units(id),
    release_date TEXT,
    release_time TEXT,
    forecast_year INTEGER,
    forecast_month INTEGER
);

CREATE TABLE table_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_title TEXT NOT NULL,
    commodity TEXT,
    scope TEXT,
    UNIQUE(report_title, commodity)
);

CREATE TABLE regions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES regions(id)
);

CREATE TABLE units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    base_unit TEXT NOT NULL,
    multiplier REAL NOT NULL DEFAULT 1.0,
    description TEXT
);
```

---

## Dashboard Table: v_wasde Pivot

**Goal:** Display `v_wasde` data as a pivot table with:
- **Filters:** report_title, scope, commodity, report_date, market_year
- **Rows:** `attribute` (each report value/measure)
- **Columns:** `region` (each country/state)
- **Cell values:** `value`

### Query Pattern
```sql
SELECT attribute, region, value, unit
FROM v_wasde
WHERE report_title = ? AND scope = ? AND commodity = ?
  AND report_date = ? AND market_year = ?
ORDER BY attribute, region
```
Then pivot in Python/pandas: `df.pivot_table(index='attribute', columns='region', values='value')`

### Data Quality Notes
- Some attributes have inconsistent casing (e.g. "Beginning Stocks" vs "Beginning stocks") — may need normalization
- `wasde_data_rough` is currently empty; all data is `data_source='final'`
- Filtering by report title matters: some commodities occur in more than one report table.
