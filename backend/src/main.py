import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from src.db import DEFAULT_DB_PATH, create_tables, get_connection
from src.csv_loader import load_csv_files
from src.nass_loader import (
    load_prospective_plantings,
    load_acreage_reports,
    load_grain_stocks,
    load_crop_progress,
)
from src.txt_loader import load_txt_report, promote_rough

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def download_csvs():
    """Run the bash script to download any missing WASDE CSV files."""
    script = SCRIPTS_DIR / "download_csvs.sh"
    if not script.exists():
        logger.error("Download script not found: %s", script)
        sys.exit(1)
    logger.info("Downloading missing CSV files...")
    result = subprocess.run(["bash", str(script)], check=False)
    if result.returncode != 0:
        logger.error("CSV download script failed with exit code %d", result.returncode)
        sys.exit(1)
    logger.info("CSV download complete.")


def main():
    parser = argparse.ArgumentParser(description="WASDE data loader")
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path"
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Full rebuild: download CSVs, recreate DB, load all data",
    )
    parser.add_argument(
        "--download", action="store_true",
        help="Download any missing CSV files (uses current date)",
    )
    parser.add_argument(
        "--load-csv", action="store_true",
        help="Load CSV files from data/csv/ into the database",
    )
    parser.add_argument(
        "--nass", action="store_true",
        help="Load NASS Prospective Plantings data",
    )
    parser.add_argument(
        "--acreage", action="store_true",
        help="Load NASS Acreage report data",
    )
    parser.add_argument(
        "--grain-stocks", action="store_true",
        help="Load NASS Grain Stocks report data",
    )
    parser.add_argument(
        "--crop-progress", action="store_true",
        help="Load NASS Crop Progress report data",
    )
    parser.add_argument(
        "--nass-start-year", type=int, default=2010,
        help="Start year for NASS report loads (default: 2010)",
    )
    parser.add_argument(
        "--rough", action="store_true",
        help="Load rough/preliminary TXT report into wasde_data_rough table",
    )
    parser.add_argument(
        "--promote-rough", action="store_true",
        help="Download finalized TXT report to wasde_data, keep rough for comparison",
    )
    parser.add_argument(
        "--txt-file", type=str, default=None,
        help="Path to local .txt file (instead of downloading from USDA)",
    )
    parser.add_argument(
        "--txt-month", type=int, default=None,
        help="Month for TXT report (default: current month)",
    )
    parser.add_argument(
        "--txt-year", type=int, default=None,
        help="Year for TXT report (default: current year)",
    )
    parser.add_argument(
        "--rebuild-table", type=str, default=None,
        help="Drop and recreate a specific table (empty)",
    )
    args = parser.parse_args()

    if not (args.rebuild or args.download or args.load_csv or args.nass
            or args.acreage or args.grain_stocks or args.crop_progress
            or args.rough or args.promote_rough
            or args.rebuild_table):
        parser.print_help()
        sys.exit(1)

    # --rebuild implies download + load-csv + all NASS
    if args.rebuild:
        # Delete existing database for a clean rebuild
        db_path = Path(args.db)
        if db_path.exists():
            logger.info("Removing existing database: %s", db_path)
            db_path.unlink()

        args.download = True
        args.load_csv = True
        args.nass = True
        args.acreage = True
        args.grain_stocks = True
        args.crop_progress = True

    if args.download:
        download_csvs()

    conn = get_connection(args.db)
    create_tables(conn)

    try:
        if args.load_csv:
            logger.info("Loading CSV files into database...")
            total = load_csv_files(conn)
            logger.info("CSV load complete: %d total rows", total)

        if args.nass:
            logger.info(
                "Loading NASS Prospective Plantings (from %d)...",
                args.nass_start_year,
            )
            rows = load_prospective_plantings(conn, start_year=args.nass_start_year)
            logger.info("NASS Prospective Plantings load complete: %d rows", rows)

        if args.acreage:
            logger.info("Loading NASS Acreage (from %d)...", args.nass_start_year)
            rows = load_acreage_reports(conn, start_year=args.nass_start_year)
            logger.info("NASS Acreage load complete: %d rows", rows)

        if args.grain_stocks:
            logger.info("Loading NASS Grain Stocks (from %d)...", args.nass_start_year)
            rows = load_grain_stocks(conn, start_year=args.nass_start_year)
            logger.info("NASS Grain Stocks load complete: %d rows", rows)

        if args.crop_progress:
            logger.info("Loading NASS Crop Progress (from %d)...", args.nass_start_year)
            rows = load_crop_progress(conn, start_year=args.nass_start_year)
            logger.info("NASS Crop Progress load complete: %d rows", rows)

        if args.rough:
            from datetime import datetime
            now = datetime.now()
            month = args.txt_month or now.month
            year = args.txt_year or now.year
            logger.info("Loading rough TXT for %02d/%d into wasde_data_rough...", month, year)
            rows = load_txt_report(
                conn, month, year,
                txt_path=args.txt_file,
                target_table="wasde_data_rough",
            )
            logger.info("Rough TXT load complete: %d rows", rows)

        if args.promote_rough:
            from datetime import datetime
            now = datetime.now()
            month = args.txt_month or now.month
            year = args.txt_year or now.year
            logger.info("Promoting rough data for %02d/%d: loading finalized TXT to wasde_data...", month, year)
            rows = promote_rough(
                conn, month, year,
                txt_path=args.txt_file,
            )
            logger.info("Promotion complete: %d rows loaded to main table", rows)

        if args.rebuild_table:
            table = args.rebuild_table
            logger.info("Rebuilding table: %s", table)
            from src.db import rebuild_table
            rebuild_table(conn, table)
            logger.info("Table %s dropped and recreated (empty)", table)

    finally:
        conn.close()

    logger.info("Done.")


if __name__ == "__main__":
    main()
