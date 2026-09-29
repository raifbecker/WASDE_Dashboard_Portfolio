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
