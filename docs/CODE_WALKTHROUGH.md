# How to run and understand this project

This guide describes the repaired version of the published WASDE Dashboard Portfolio,
not the separate `wasde-lakehouse` project. The Flask UI and SQLite design remain.

## The short explanation

USDA publishes spreadsheets as CSV files. The backend downloads them, turns their
rows into database observations, and stores those observations in SQLite. The
frontend reads that database through Flask API endpoints. Browser JavaScript calls
those endpoints to populate dropdowns, tables, charts, and CSV exports.

```text
USDA CSV -> saved CSV -> validate all rows -> one SQLite transaction
                                              |
                              dimensions + observations + source hash
                                              |
                                     SQL views -> Flask API -> browser
```

Loading and displaying are separate jobs. Starting Flask does not download data.
Closing Flask does not delete the database. Loading new reports updates the same
database; refreshing the browser lets you see them.

## First run on Windows, without a USDA download

Open PowerShell in this repository's root (the folder containing `backend`,
`frontend`, `tests`, and this README). These commands use one environment for both
halves so there is no activation or environment switching to remember:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt -r frontend/requirements.txt
Set-Location backend
..\.venv\Scripts\python.exe -m src.main --load-csv --csv-dir ../tests/fixtures
Set-Location ../frontend
..\.venv\Scripts\python.exe app.py
```

Open http://localhost:5000. Select **World Corn Supply and Use -> World -> Corn**
in Regional Comparison, then select an available market year and report date.
The bundled fixture is the complete May 2025 report, with 4,988 observations.
It is historical sample data. One report cannot demonstrate month-to-month changes;
load more months for that. Press Ctrl+C in the serving terminal to stop Flask.
The built-in Flask server is for local use.

The Linux/macOS equivalents use `.venv/bin/python` instead of
`.venv/Scripts/python.exe`, and `cd` instead of `Set-Location`.

## Normal use after setup

From the repository root, first move into `backend`:

```powershell
Set-Location backend
# Fetch missing monthly reports, then load new or changed local CSVs.
..\.venv\Scripts\python.exe -m src.main --download --load-csv
# Re-read files already on disk, without contacting USDA.
..\.venv\Scripts\python.exe -m src.main --load-csv
# Re-fetch existing remote files as well, so USDA corrections can be detected.
..\.venv\Scripts\python.exe -m src.main --refresh-csv
```

The live download range starts in January 2021 and ends in the current month.
A refresh can take time because it requests all months, even ones already cached.
The downloader follows the archive’s actual links, including versioned filenames.
Verified May/June 2026 V2 links are retained as fallbacks if the archive HTML is blocked.
404s are reported as unavailable. Other download/validation failures cause the
command to fail; the database load does not start in that combined run. Files
successfully downloaded earlier in the run stay cached. After resolving the
failure, rerun the command, or explicitly run `--load-csv` to publish the local files.

To refresh a smaller range, from `backend`:

```powershell
..\.venv\Scripts\python.exe scripts/download_csv.py data/csv 2025 2025 7 --refresh
..\.venv\Scripts\python.exe -m src.main --load-csv
```

This requests January through July 2025. There is no implicit start month option.
The main downloader uses Python directly; Git Bash is not needed for these commands.

To start the dashboard in another terminal, from the repository root:

```powershell
Set-Location frontend
..\.venv\Scripts\python.exe app.py
```

## Where your files live

- `backend/data/csv/`: downloaded reports. `--csv-dir` selects another input directory.
- `backend/data/wasde.db`: default SQLite database. Both backend and frontend use this path.
- `frontend/settings.json`: the database path used by Flask, relative to `frontend`.
- `--db some/path.db`: loader override, relative to the terminal's current directory.
  If you choose a different database, update `frontend/settings.json` to match.
- `tests/fixtures/`: a historical public report used for regression tests and the offline first run.

Do not use `--rebuild` for normal updates. It deletes the selected database and
also triggers WASDE and NASS backfills. `--load-csv` is enough to replay saved CSVs.

## Follow one observation through the code

Imagine a row saying: May report, Corn, World, 2025/26, Ending Stocks,
Million Metric Tons, Annual, value 277.83 (illustrative value).

1. `backend/src/main.py` reads your CLI flags and opens the database. It calls
   `create_tables` to create or migrate tables and refresh SQL views.
2. With `--download`, `backend/scripts/download_csv.py` requests missing files.
   With `--refresh-csv`, it requests existing files too. It validates the CSV and
   report month, writes a temporary file, then replaces the cached file in one
   operation. A bad download leaves the old cached file intact.
3. `backend/src/csv_loader.py` reads the CSV bytes once, hashes them with SHA-256,
   and parses those same bytes. A hash is a fingerprint of the bytes, not proof
   that USDA's figures are correct.
4. It validates the entire CSV: required columns, valid dates, required identity
   fields, numeric values, and report month. Missing values stay missing. Invalid
   values fail the source instead of quietly becoming zero or disappearing.
5. It opens one transaction (`BEGIN IMMEDIATE`). If the source hash and stored row
   count match the last load, it skips the file.
6. It looks up or creates IDs in `table_types`, `regions`, and `units`, then writes
   observations in `wasde_data`. `table_types` identifies report title + commodity;
   the value belongs in the fact table, not in a lookup table.
7. It records the raw accepted file in `csv_versions`, points `csv_sources` at the
   current hash, and appends a truthful count to `load_log`. Then it commits once.
   Any exception rolls the source transaction back, including dimension inserts.
8. `backend/sql/views/v_wasde.sql` joins IDs back to readable names and includes
   preliminary TXT rows only where the corresponding final series is missing.
9. `frontend/app.py` queries the annual display view `v_dashboard`. It binds selected
   filters as SQL parameters. It returns JSON; pandas reshapes observations into
   rows and columns. It rejects a measure with multiple units instead of picking one.
10. `frontend/templates/index.html` calls those APIs using jQuery, renders tables
    with DataTables, and draws charts with Chart.js. Those libraries are stored locally.

## What makes one observation distinct?

The database key is:

```text
report date + report/commodity ID + region ID + measure + market year
+ unit + annual/quarterly period + reliability statistic
```

The numeric value is NOT part of that key: a corrected value should replace the old
value for the same observation. Projection/estimate flags are metadata; they can
change as estimates mature. An annual production figure and a quarterly production
figure must not collide. Two different reliability statistics must not collide.
An exact repeated row is counted once. Conflicting rows for the same key reject
the entire CSV so the source is not partly published.

## What "atomic" means here

Suppose the current CSV has 5,000 rows, and a corrected CSV changes 12 values and
removes one row. The loader replaces only the observations owned by that source
file. If loading row 4,000 fails, SQLite rolls the transaction back: the old snapshot
is still available. The source hash and success log cannot advance ahead of the data.
Other report months remain in place. Accepted older raw CSV versions remain archived.
This is one transaction per file, not one transaction for the entire download history.

## Revisions: report date versus market year

A report date is when an estimate was published. A market year is the crop season
the estimate describes. Reports in May and June may both estimate the same 2025/26
crop season. Comparing them is a revision to an estimate, not a daily price change.

`v_revision_history` uses `LAG(value)` to find the previous available report for
exactly the same commodity, region, measure, market year, unit, period, and statistic.
`v_month_over_month` derives changes and percent changes from that view. If a month
is missing, it uses the previous available report. A zero baseline has no percent change.
`v_latest_estimates` chooses the latest report for each complete series.

The dashboard's World chart gives each market-year series a 12-month window
starting at its earliest available report month. A July start runs July through
June of the next year; a March start runs March through the next February. Later
reports remain in the database and time table but are outside that chart window.
Missing months stay blank. The latest report within a calendar year/month is used
if several exist; a missing value is not filled from an earlier report.

When starts differ, the shared axis is Month 1 through Month 12. When all starts
match, it uses the rotated month names. Each legend entry includes its actual
start/end months, and tooltips show the actual report date. Chart summaries use
this same window. The first available report can be later than the true first
publication if older source data is absent; it is not an inferred crop-calendar date.
SQL revision views and the time table retain the full chronological report sequence.

## Existing databases and priority 3

Run `--load-csv` against your saved reports after updating this code. The schema
migration adds the missing fields and replaces the narrow indexes. Replaying CSVs
restores records that the earlier loader discarded; schema changes alone cannot.

Old `load_log` entries are no longer enough to skip CSVs. On the first replay of a
normally named WASDE monthly CSV, legacy final WASDE rows for that month are replaced
by the validated CSV snapshot, including final TXT rows. Preliminary rows are kept,
and NASS rows are retained. CSV is the authoritative final source for that month.
Back up an existing database with SQLite's backup API before migrating it, and retain
its original CSV files. No existing personal database was modified during development.

Priority 3 was two separate skips: the downloader skipped existing files, and the
loader skipped names in `load_log`. `--refresh-csv` fixes the first; SHA-256 comparison
fixes the second. This does not automatically poll USDA; you choose when to refresh.

NASS, TXT, and XML loaders are separate existing paths. They still use their previous
load-log behavior and are not covered by the CSV transaction/versioning guarantee.
Quarterly and reliability records are queryable in SQL; this change does not add
quarter/statistic controls to the dashboard.

## A short interview explanation

"The backend downloads public USDA reports and stores normalized observations in
SQLite. I preserve the dimensions needed to identify a fact, hash each CSV to detect
changes, and publish each source in one transaction. Corrected files replace their
own snapshot while keeping old raw versions. SQL window functions compare the same
series across reports. Flask serves filtered JSON to a local dashboard. I use a full
USDA fixture and failure tests to check record retention and rollback behavior."

## Running checks

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -m "backend or api" -q
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD/.venv/playwright-browsers"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
```

The tests use disposable databases, mocked downloads, and a loopback browser server;
they do not load your personal database or contact USDA.
