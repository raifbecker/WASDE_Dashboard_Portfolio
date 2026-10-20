# WASDE Dashboard Portfolio
<img width="3762" height="1258" alt="image" src="https://github.com/user-attachments/assets/903fcc74-3ab5-4c9d-b9d0-20b4112e119b" />
<img width="3774" height="954" alt="image" src="https://github.com/user-attachments/assets/2fcac7dc-6886-4598-a4e1-6ddfba0d0e0f" />

A Flask dashboard for exploring USDA World Agricultural Supply and Demand Estimates (WASDE) data. The backend downloads monthly USDA CSV reports into SQLite; the frontend shows regional tables and world time comparisons.

Start with the [plain-English code and usage guide](docs/CODE_WALKTHROUGH.md), including Windows PowerShell commands and an offline sample. See [integrity changes and verification](docs/INTEGRITY_FIXES.md).

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

Open <http://localhost:5000>. The frontend's `settings.json` points to `backend/data/wasde.db` using a relative path. The database and downloaded CSVs live under `backend/data/` and are intentionally excluded from Git. A fresh clone needs a backend load before the dashboard can show data; use the offline fixture walkthrough or the live pull above.

The first live pull starts at January 2021. HTTP 404 months are reported and skipped; other download or validation failures cause a nonzero exit. A successful exit still does not prove complete historical coverage. CSVs are validated before the cached file is replaced. The loader hashes their bytes and atomically replaces changed source snapshots, retaining accepted raw versions. Use `python -m src.main --refresh-csv` from `backend/` to check existing remote files for corrections. See the [integrity changes](docs/INTEGRITY_FIXES.md).

## Project files

- [Backend guide](backend/README.md): loader commands, scheduled jobs, and database schema.
- [Frontend app](frontend/app.py) and [template](frontend/templates/index.html): Flask API and dashboard UI.
- [Schema note](frontend/SCHEMA_DISCOVERY.md): schema and a dated local database snapshot.

Data source: [USDA WASDE](https://www.usda.gov/oce/commodity/wasde). This repository includes one historical USDA CSV in `tests/fixtures/` for reproducible tests and an offline first run. It does not include a prebuilt database or a complete report archive.

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

The dashboard displays annual estimates. Quarterly observations and reliability statistics are retained in SQL but excluded from its existing charts and tables. Mixed units for one measure produce an explicit API error rather than an arbitrary value.

## FAQ

### Where is the October 2025 report?

USDA canceled the October 2025 WASDE report because of the federal government
shutdown. No report was published for that month, so the dashboard leaves a gap
between September and November rather than filling in an estimate. This is an
expected gap, not a download failure. See [contemporaneous reporting](https://www.dtnpf.com/agriculture/web/ag/news/article/2025/10/09/watching)
and the [USDA historical archive](https://www.usda.gov/historical-wasde-report-data-3).
