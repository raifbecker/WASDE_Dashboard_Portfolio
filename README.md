# WASDE Dashboard Portfolio

A Flask dashboard for exploring USDA World Agricultural Supply and Demand Estimates (WASDE) data. The backend downloads monthly USDA CSV reports into SQLite; the frontend shows regional tables and world time comparisons.

## Run locally

Use Python 3.9 or newer. From the repository root:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.main --db data/wasde.db --download --load-csv
deactivate

cd ../frontend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open <http://localhost:5000>. The frontend's `settings.json` points to `backend/data/wasde.db` using a relative path. The database and downloaded CSVs live under `backend/data/` and are intentionally excluded from Git. A fresh clone needs the backend pull before the dashboard can show data.

The first pull starts at January 2021. The downloader skips unavailable monthly files and retries them on later runs. Check the console output for missing months; a successful exit currently does not guarantee complete coverage. See the [download verification plan](backend/DOWNLOAD_VERIFICATION_PLAN.md) for the proposed integrity improvements.

## Project files

- [Backend guide](backend/README.md): loader commands, scheduled jobs, and database schema.
- [Frontend app](frontend/app.py) and [template](frontend/templates/index.html): Flask API and dashboard UI.
- [Schema note](frontend/SCHEMA_DISCOVERY.md): schema and a dated local database snapshot.

Data source: [USDA WASDE](https://www.usda.gov/oce/commodity/wasde). This repository contains code and documentation; it does not include USDA report files or a prebuilt database.

## Run the tests

The tests use disposable SQLite databases and mocked downloads. They never read or modify `backend/data/wasde.db` or contact USDA. Use a separate virtual environment at the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
PLAYWRIGHT_BROWSERS_PATH=.venv/playwright-browsers .venv/bin/python -m playwright install chromium
```

On Linux, Chromium also needs system libraries. If launch reports a missing `.so` file, install them with `.venv/bin/python -m playwright install-deps chromium` (requires system package installation privileges). The browser tests also recognize libraries extracted under `.venv/chromium-libs/usr/lib/x86_64-linux-gnu` if system installation is unavailable.

Run each layer, then the complete suite:

```bash
.venv/bin/python -m pytest -m backend -q
.venv/bin/python -m pytest -m api -q
.venv/bin/python -m pytest -m browser -q
.venv/bin/python -m pytest -q
```

The browser tests launch a temporary Flask server on `127.0.0.1` and headless Chromium. They block and record requests outside that server; all dashboard JavaScript and CSS is served locally. If the browser suite fails before loading the page, confirm that Chromium was installed into `.venv/playwright-browsers`, the Linux libraries are present, and local loopback connections are allowed. A failed API or loader test can be rerun by name with `.venv/bin/python -m pytest tests/test_api.py::test_regional_pivot_and_empty_result -q`.

The shared fixture in `tests/conftest.py` seeds U.S. and World reports, multiple regions and dates, two market years, and a missing value. Loader tests use small CSV, TXT, and XML samples plus mocked NASS and HTTP responses. Browser tests exercise filter changes, tables, chart statistics, exports, empty data, and an API failure.
