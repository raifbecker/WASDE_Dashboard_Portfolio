# Integrity changes and verification

Verification recorded on October 20, 2026, against changes based on repository
commit `9a3b9e5`. Results below describe the local checks, not a remote CI run.

## What changed

1. Complete observation keys preserve units, annual/quarterly periods, and reliability
   statistics. Commodity remains part of the report-type lookup. Exact duplicate
   observations count once; conflicting duplicates reject the entire CSV.
2. A CSV is validated, then published in one SQLite transaction. Dimension inserts,
   snapshot replacement, accepted raw bytes, SHA-256, and the success log commit
   together. Inner helpers do not commit. Invalid replacements retain the last good
   data. Counts describe distinct observations actually published, not attempted inserts.
3. Each CSV owns its observations. Changed content replaces that file's complete
   snapshot, including removals. Other report months remain intact. Accepted raw
   versions are archived in `csv_versions`; `csv_sources` identifies the current one.
4. Revision, month-over-month, latest-estimate, supply/use and rough/final views use
   compatible dimensions. Revision comparisons use the prior available report, with
   no percent change from zero. View definitions refresh for existing databases.
5. Download refresh is explicit: `--refresh-csv` checks existing remote files, whereas
   `--load-csv` only checks local files. CSV downloads validate before atomic cached-file
   replacement. The normal Python CLI no longer requires Bash. Its default database
   now matches the frontend: `backend/data/wasde.db`.
6. `v_dashboard` explicitly selects annual estimates, leaving quarterly and reliability
   rows in SQL. API tables and charts reject mixed units for the same measure.
7. Existing schemas receive additive columns and replacement unique indexes. Replay
   saved monthly CSVs to restore previously discarded observations. For the first
   replay, CSV is authoritative over legacy final WASDE/TXT rows for its month;
   NASS rows and preliminary rows are retained. Back up a personal DB first.
8. Fixed the Windows test's unclosed SQLite connection and the browser test's early
   assertion before its table was rendered.

## Evidence

- Complete suite: **48 passed**, including 4 real Chromium browser tests.
- Full public May 2025 fixture: **4,988 records published and stored**, versus 4,328
  stored by the original loader on that same fixture.
- Retained: 120 quarterly observations and 630 reliability-statistic observations.
- Identical rerun: 0 records republished.
- Fixture bytes and archived raw bytes verified with SHA-256.
- SQLite integrity and foreign keys passed.
- New regressions cover source corrections/removals, exact/conflicting duplicates,
  malformed replacement rejection, simulated storage and log failures, a reader
  during replacement, legacy schema/replay, rebuilt-table replay, source ownership,
  revision dimension boundaries, mixed-unit rejection, and remote-refresh dispatch.
- Tests use disposable databases and mocked downloads. No new live USDA backfill,
  Docker run, remote CI run, or personal-database migration is claimed.

## Scope and remaining limits

This applies to the WASDE CSV path. The separate NASS/TXT/XML loaders retain their
existing load-log and transaction behavior. This does not migrate them into the
Lakehouse architecture. Invalid CSVs fail visibly; there is no new quarantine UI
or persistent failure-run table. A single CSV is atomic, not the entire date range.
Archived raw bytes are in SQLite, rather than an external versioned object store.

The current UI displays annual estimates; it has no new quarter or reliability
selectors. Each chart series runs for 12 chronological months from its first
available report. July runs to the following June, with gaps left blank. Different
starts align at Month 1; legend ranges and tooltip dates retain their calendar
meaning. Summaries use the same window. Later observations remain in the database,
SQL views, and time table. API and browser regressions verify the calendar-year
boundary, arbitrary starting months, missing months, and the excluded 13th month.
A SHA-256 comparison detects changed bytes, not agricultural truth. Historical 404
months remain explicit gaps; a successful download command is not a completeness
certificate. Refreshing remote corrections is manual unless scheduled separately.

## Re-run

From the repository root, with test dependencies and Chromium installed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

See [the code and usage guide](CODE_WALKTHROUGH.md) for an offline first run,
normal updates, refresh commands, and the code path to follow.


## Versioned archive files (October 20 follow-up)

The standard filenames for May and June 2026 returned 404, but USDA's published
archive links end in `2026-05-V2.csv` and `2026-06-V2.csv`. Both verified files
are now loaded. The local live database has 346,598 observations across 69 report
months from January 2021 through October 2026. October 2025 is not listed in the
USDA archive and remains a gap; no synthetic value is inserted.

The downloader now parses monthly CSV links from USDA's historical archive rather
than assuming all filenames follow the unsuffixed pattern. The archive HTML returned
403 to Python during verification even though its CSVs worked; verified May/June V2
URLs therefore also exist as explicit fallbacks. Future unlisted corrections still
require archive access or updating that fallback mapping. Use `--refresh-csv` to
recheck files already cached. Versioned URLs are saved under stable local monthly
names so corrections replace a source rather than create duplicate sources.

Sources:
- https://www.usda.gov/historical-wasde-report-data-3
- https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-2026-05-V2.csv
- https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-2026-06-V2.csv

The Argentina wheat 2025/26 Beginning Stocks chart has a missing October 2025
observation between September (4.94) and November (3.41). This visible break is an
absent report, not a split commodity series. Future months also remain blank.
