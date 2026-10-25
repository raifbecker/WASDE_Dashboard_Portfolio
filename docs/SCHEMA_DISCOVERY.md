# Database schema

The default SQLite database is `backend/data/wasde.db`. It is created by the loader and excluded from Git.

## Tables

| Table | Purpose |
| --- | --- |
| `wasde_data` | Final observations, including report date, market year, measure, value, unit, period, reliability statistic, and CSV source filename. |
| `wasde_data_rough` | Preliminary observations with the same dimensions as final observations. |
| `table_types` | Report title, commodity, and scope. Unique on report title and commodity. |
| `regions` | Region names and optional parent region IDs. |
| `units` | Unit names, categories, base units, and conversion multipliers. |
| `load_log` | Source filenames, load timestamps, and row counts. |
| `csv_versions` | Accepted raw CSV bytes and SHA-256 hashes. Primary key: source filename and hash. |
| `csv_sources` | Current accepted hash and observation count for each CSV source filename. References `csv_versions`. |

Observation uniqueness includes report date, table type, region, attribute, market year, unit, annual/quarterly period, and reliability statistic. CSV observations also retain their source filename in `csv_source_file`.

Table definitions live in [backend/sql/tables](../backend/sql/tables/). CSV tracking tables and observation indexes are created by [backend/src/db.py](../backend/src/db.py).

## Views

| View | Purpose |
| --- | --- |
| `v_wasde` | Joins observations to lookup tables. Includes preliminary observations where the matching final observation is missing. |
| `v_dashboard` | Filters `v_wasde` to annual observations with an empty reliability statistic. Used by the Flask dashboard. |
| `v_latest_estimates` | Latest observation for each series. |
| `v_revision_history` | Changes between successive available reports for a series. |
| `v_month_over_month` | Changes and percentage changes from the previous available report. |
| `v_supply_use_balance` | Supply/use measures pivoted into columns. |
| `v_rough_vs_final` | Preliminary and final values side by side. |
| `v_world_with_hierarchy` | Region hierarchy built with a recursive CTE. |

Definitions live in [backend/sql/views](../backend/sql/views/).

## Dashboard query

```sql
SELECT attribute, region, value, unit
FROM v_dashboard
WHERE report_title = ? AND scope = ? AND commodity = ?
  AND report_date = ? AND market_year = ?
ORDER BY attribute, region;
```

The Flask app binds the filter values, checks that each measure has a single unit, and pivots attributes into rows and regions into columns. Quarterly and reliability observations remain available through `v_wasde`.
