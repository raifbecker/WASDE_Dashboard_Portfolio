import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "wasde.db"


def verify(db_path=None):
    path = db_path or DB_PATH
    if not Path(path).exists():
        print(f"Database not found: {path}")
        sys.exit(1)

    conn = sqlite3.connect(str(path))

    print("=== WASDE Database Verification ===\n")

    # 1. Total rows
    (total,) = conn.execute("SELECT COUNT(*) FROM wasde_data").fetchone()
    print(f"Total wasde_data rows: {total:,}")

    # 2. Table types
    tt_rows = conn.execute(
        "SELECT id, report_title, commodity, scope FROM table_types ORDER BY id"
    ).fetchall()
    print(f"\nTable types ({len(tt_rows)}):")
    for row in tt_rows:
        print(f"  [{row[0]:3d}] {row[1]}  (commodity={row[2]}, scope={row[3]})")

    # 3. Region count
    (region_count,) = conn.execute("SELECT COUNT(*) FROM regions").fetchone()
    print(f"\nRegions: {region_count}")
    regions = conn.execute(
        "SELECT name FROM regions ORDER BY name LIMIT 20"
    ).fetchall()
    for (name,) in regions:
        print(f"  - {name}")
    if region_count > 20:
        print(f"  ... and {region_count - 20} more")

    # 4. Date range
    row = conn.execute(
        "SELECT MIN(report_date), MAX(report_date) FROM wasde_data"
    ).fetchone()
    print(f"\nReport date range: {row[0]} to {row[1]}")

    # 5. Rows per source
    print("\nLoad log:")
    log_rows = conn.execute(
        "SELECT source, source_file, rows_loaded, loaded_at FROM load_log ORDER BY loaded_at"
    ).fetchall()
    for src, sf, rl, la in log_rows:
        print(f"  {src:4s} | {sf:30s} | {rl:>8,} rows | {la}")

    # 6. Rows by scope
    print("\nRows by scope:")
    scope_rows = conn.execute(
        """SELECT t.scope, COUNT(*)
           FROM wasde_data d JOIN table_types t ON d.table_type_id = t.id
           GROUP BY t.scope"""
    ).fetchall()
    for scope, cnt in scope_rows:
        print(f"  {scope or 'NULL':10s}: {cnt:>10,}")

    # 7. Sample query: v_wasde
    print("\nSample from v_wasde (5 rows):")
    sample = conn.execute(
        "SELECT report_date, report_title, region, attribute, market_year, value, unit "
        "FROM v_wasde LIMIT 5"
    ).fetchall()
    for r in sample:
        print(f"  {r}")

    # 8. Sample balance sheet
    print("\nSample v_supply_use_balance (3 rows):")
    balance = conn.execute(
        """SELECT report_date, report_title, region, market_year,
                  beginning_stocks, production, imports, exports, domestic_use, ending_stocks
           FROM v_supply_use_balance
           WHERE production IS NOT NULL
           LIMIT 3"""
    ).fetchall()
    for r in balance:
        print(f"  {r}")

    # 9. NASS report counts
    nass_reports = conn.execute(
        """SELECT t.report_title, COUNT(*) as cnt
           FROM wasde_data d
           JOIN table_types t ON d.table_type_id = t.id
           WHERE t.report_title IN ('Prospective Plantings', 'Acreage', 'Grain Stocks', 'Crop Progress')
           GROUP BY t.report_title
           ORDER BY t.report_title"""
    ).fetchall()
    print(f"\nNASS report rows:")
    for title, cnt in nass_reports:
        print(f"  {title}: {cnt:,}")

    if nass_reports:
        nass_range = conn.execute(
            """SELECT MIN(d.report_date), MAX(d.report_date)
               FROM wasde_data d JOIN table_types t ON d.table_type_id = t.id
               WHERE t.report_title IN ('Prospective Plantings', 'Acreage', 'Grain Stocks', 'Crop Progress')"""
        ).fetchone()
        print(f"  Date range: {nass_range[0]} to {nass_range[1]}")

        nass_commodities = conn.execute(
            """SELECT t.report_title, t.commodity, COUNT(*) as cnt
               FROM wasde_data d
               JOIN table_types t ON d.table_type_id = t.id
               WHERE t.report_title IN ('Prospective Plantings', 'Acreage', 'Grain Stocks', 'Crop Progress')
               GROUP BY t.report_title, t.commodity
               ORDER BY t.report_title, t.commodity"""
        ).fetchall()
        print("  Commodities:")
        for title, commodity, cnt in nass_commodities:
            print(f"    {title} / {commodity}: {cnt:,}")

        nass_sample = conn.execute(
            """SELECT d.report_date, t.report_title, t.commodity, r.name, d.attribute,
                      d.market_year, d.value, d.unit
               FROM wasde_data d
               JOIN table_types t ON d.table_type_id = t.id
               JOIN regions r ON d.region_id = r.id
               WHERE t.report_title IN ('Prospective Plantings', 'Acreage', 'Grain Stocks', 'Crop Progress')
               ORDER BY d.report_date DESC
               LIMIT 5"""
        ).fetchall()
        print("  Sample (5 rows):")
        for r in nass_sample:
            print(f"    {r}")

    conn.close()
    print("\n=== Verification complete ===")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else None
    verify(db)
