# WASDE download verification plan

This is a work guide for improving the monthly WASDE CSV pull. It describes the changes to make and how to check them; it does not implement them.

## Goal and limits

Use SHA-256 to detect a damaged or changed local CSV. Keep a digest for each accepted download and check the file against that digest before the loader reads it.

A SHA-256 digest only proves that two sets of bytes match. A digest calculated from the same damaged download does not prove that USDA published those bytes. Unless USDA supplies an independent trusted checksum, keep HTTPS and validate the response and CSV content as well. A stored digest also cannot tell whether the remote file has changed without a fresh GET or a reliable conditional request.

## Current behavior to account for

- `scripts/download_csv.py` requests monthly files from 2021 onward. It skips any path that exists, without checking its contents. It writes response bytes directly to the final path and reports an empty response or request failure as unavailable.
- `scripts/download_csvs.sh` calls that downloader. `src/main.py --download --load-csv` then loads every CSV that `src/csv_loader.py` finds.
- `src/csv_loader.py` skips a filename already listed in SQLite `load_log`. That table records the filename and row count, but no hash. Changing an already loaded CSV on disk will therefore **not** update the database on the next run.
- `scripts/download_latest_csv.sh` is a separate curl path with its own existence check. Decide whether to remove it or make it call the same verified downloader, so it cannot bypass validation.
- The scheduled WASDE job is `cron/update_wasde_csv.sh`. Docker mounts `data/` from the host, and Git ignores that directory.

The first pull saved 66 monthly CSVs through September 2026. October 2025 and May–June 2026 were absent after two attempts. The downloader currently exits successfully even when some requested months are unavailable, so the job needs a clear way to report incomplete coverage.

## Work through the changes

### 1. Choose the checksum record

Keep one manifest under `data/`, for example `data/csv/manifest.json`. For each accepted file, record at least:

| Field | Purpose |
| --- | --- |
| Filename and source URL | Identify the month and its origin |
| SHA-256 hex digest | Detect later byte changes |
| Byte length | Catch obvious truncation and aid diagnosis |
| Fetch time | Show when the local copy was obtained |
| HTTP ETag and Last-Modified, if supplied | Support conditional checks; do not treat either as a checksum |

Write the manifest atomically, just like the CSV. Keep it in `data/` with the downloaded files; back it up with the database if the data is moved to another machine. Avoid storing a digest inside `load_log` as the *only* copy until there is a clear policy for a changed source file.

### 2. Validate before accepting a download

For each requested month, download to a temporary file in `data/csv/` and stream its bytes through SHA-256. Accept it only after all checks pass:

1. HTTP succeeded and the response is nonempty.
2. The response looks like a CSV, not an HTML error page returned with HTTP 200. Check the expected WASDE column names, including `WasdeNumber`, `ReportDate`, `ReportTitle`, `Commodity`, and `Value`.
3. CSV parsing succeeds; there is at least one data row; and its report month matches the requested filename. Use the report's actual date field for this check.
4. The final byte count and SHA-256 match the values computed while reading the temporary file.

Only then replace the final CSV path and update the manifest. On any failure, remove the temporary file and leave an existing verified CSV untouched. Preserve the HTTP status or exception type in the result; do not call every request failure a timeout.

### 3. Verify existing files and check for remote changes

- Before skipping an existing CSV, compare its current SHA-256 and byte length with the manifest. If either differs, stop using that file and report a checksum mismatch. Do not silently overwrite it.
- If a file has no manifest entry, treat it as unverified. For the 66 files from the first pull, either download fresh copies and validate them, or validate their CSV structure and dates before recording baseline hashes. A baseline hash alone does not establish that the original bytes were correct.
- To learn whether USDA changed a published file, fetch it again and compare SHA-256 digests. A conditional GET may avoid a full transfer when the server supplies usable validators, but the downloaded bytes remain the deciding comparison when a new response is received.
- Set an explicit refresh policy. One option is to recheck the current and previous month on every scheduled run and audit older months periodically. Rehashing local files checks local integrity; it does not check the server.

### 4. Decide what a changed source means for SQLite

Define this policy before turning on remote refresh. With the current `load_log` and `INSERT OR IGNORE` loading behavior, overwriting an already loaded CSV can leave old rows in SQLite. The safe initial policy is to retain the prior verified CSV, save the new version separately for review, and report that a database rebuild is required before accepting it.

If automatic replacement is wanted later, add source-file provenance to the fact rows or another reliable way to remove exactly the old file's rows. Then replace those rows and their `load_log` entry in one database transaction. Do not use `--rebuild` casually as a narrow CSV refresh: it deletes the database and also starts the NASS pulls.

### 5. Make run results actionable

Return and log separate counts for verified unchanged files, new files, missing/unavailable months, invalid responses, checksum mismatches, and remotely changed files. Give each month a specific status. Decide which statuses fail the scheduled job: a transient missing month before publication may be expected, while an invalid CSV or local checksum mismatch should fail it.

Keep retry behavior bounded, with a delay for transient connection errors and server errors. Do not repeatedly retry a confirmed 404 in the same run. A later scheduled run can try that month again.

## Checks to perform

Use temporary test files or mocked HTTP responses; do not damage the live `data/csv/` directory.

1. Download a valid CSV. Confirm that its manifest hash equals a fresh SHA-256 of the saved file, and that the loader can read it.
2. Run again with the same remote bytes. Confirm that the status is unchanged and no duplicate rows are loaded.
3. Truncate or edit a local CSV. Confirm that verification fails before loading and the original manifest remains intact.
4. Return HTTP 200 with HTML, an empty body, and a CSV for the wrong month. Confirm that none replaces a verified file.
5. Interrupt a download. Confirm that the final CSV and manifest remain usable and no partial final file appears.
6. Return changed bytes for a previously loaded month. Confirm that the change is reported and the database is not silently left inconsistent.
7. Simulate 404 and timeout responses. Confirm that each month and the overall run report the right status.

For a final smoke check, run `python -m src.main --db data/wasde.db --download --load-csv` in the backend virtual environment, then `python scripts/verify.py data/wasde.db` and `PRAGMA integrity_check`. Confirm that the file count, missing-month list, and latest report date agree with the run summary.
