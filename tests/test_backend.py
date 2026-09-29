import importlib.util
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import requests

from src import csv_loader, db, main, nass_loader, txt_loader, xml_loader


pytestmark = pytest.mark.backend


def fresh_db(db_path):
    conn = db.get_connection(db_path)
    db.create_tables(conn)
    return conn


def test_schema_lookup_views_and_rebuild_allowlist(db_path):
    conn = fresh_db(db_path)
    db.create_tables(conn)  # repeatable setup
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM units").fetchone()[0] > 0
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert set(db.VIEW_ORDER) <= views
    region = db.get_or_create_region(conn, "u.s.")
    assert region == db.get_or_create_region(conn, "U.S.")
    assert conn.execute("SELECT name FROM regions WHERE id = ?", (region,)).fetchone()[0] == "U.S."
    assert db.get_unit_id(conn, "Million Bushels") is not None
    assert db.get_unit_id(conn, "unknown") is None
    with pytest.raises(ValueError, match="Unknown table"):
        db.rebuild_table(conn, "not_a_table")
    conn.close()


@pytest.mark.parametrize("raw,expected", [
    ("January 2026", "2026-01-01"), ("Feb 2026", "2026-02-01"),
    ("02/15/2026", "2026-02-15"), ("", None), (None, None),
])
def test_csv_date_normalization(raw, expected):
    assert csv_loader.normalize_report_date(raw) == expected


def test_csv_value_scope_and_region_helpers():
    assert csv_loader.parse_value("1,234.5") == 1234.5
    assert csv_loader.parse_value("bad") is None
    assert csv_loader.parse_int("42.0") == 42
    assert csv_loader.parse_int("") is None
    assert csv_loader.infer_scope("U.S. Corn") == "U.S."
    assert csv_loader.infer_scope("World Wheat") == "World"
    assert csv_loader.infer_scope("Unknown") is None
    assert db.normalize_region_name("major exporters") == "Major Exporter"
    assert db.normalize_region_name("u.s.") == "U.S."


def test_csv_load_and_skip_duplicate_with_distinct_commodities(db_path, tmp_path):
    conn = fresh_db(db_path)
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    name = "oce-wasde-report-data-2026-01.csv"
    (csv_dir / name).write_text(
        "ReportDate,WasdeNumber,ReportTitle,Commodity,Region,Attribute,MarketYear,Value,Unit\n"
        "January 2026,670,World Crop Supply and Use,Corn,world,Production,2025/26,10,Million Metric Tons\n"
        "January 2026,670,World Crop Supply and Use,Wheat,china,Production,2025/26,8,Million Metric Tons\n"
    )
    assert csv_loader.load_csv_files(conn, csv_dir) == 2
    assert csv_loader.load_csv_files(conn, csv_dir) == 0
    assert conn.execute("SELECT count(*) FROM wasde_data").fetchone()[0] == 2
    assert conn.execute("SELECT rows_loaded FROM load_log WHERE source_file = ?", (name,)).fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM table_types WHERE report_title='World Crop Supply and Use'").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM v_wasde WHERE commodity='Wheat'").fetchone()[0] == 1
    conn.close()


def test_nass_excerpt_parser_and_idempotent_load(db_path, monkeypatch):
    excerpt = (
        "Corn Area Planted\n"
        "State : 2025 : 2026\n"
        "----------------------------------------\n"
        "Alabama ........ : 1,200 1,300\n"
        "----------------------------------------\n"
    )
    parsed = nass_loader.parse_report_txt(excerpt, "2026-03-01")
    assert ("Prospective Plantings", "Corn", "Alabama", "Area Planted", "2026", 1300.0, "1,000 Acres") in parsed
    conn = fresh_db(db_path)
    monkeypatch.setattr(nass_loader, "fetch_releases", lambda *a, **kw: [("2026-03-01", "https://example.invalid/nass.txt")])
    monkeypatch.setattr(nass_loader, "download_report_txt", lambda url: excerpt)
    assert nass_loader.load_prospective_plantings(conn, start_year=2026) == 2
    assert nass_loader.load_prospective_plantings(conn, start_year=2026) == 0
    assert conn.execute("SELECT count(*) FROM v_wasde WHERE commodity='Corn' AND region='Alabama'").fetchone()[0] == 2
    conn.close()


TXT_SAMPLE = """WASDE - 670 - 11 January 2026
U.S. Corn Supply and Use
2024/25  2025/26 Est.  2025/26 Proj.  2025/26 Proj.
Item  Jan  Feb
========================================
Million Bushels
Production  100  110  120  130
"""


def test_txt_excerpt_parser_rough_and_final(db_path, tmp_path):
    parsed = txt_loader.parse_txt_report(TXT_SAMPLE, 2026, 1)
    assert len(parsed) == 4
    assert parsed[0][0] == "2026-01-01"
    assert parsed[0][4] == "Production"
    path = tmp_path / "wasde0126.txt"
    path.write_text(TXT_SAMPLE)
    conn = fresh_db(db_path)
    assert txt_loader.load_txt_report(conn, 1, 2026, txt_path=path, target_table="wasde_data_rough") == 4
    assert txt_loader.promote_rough(conn, 1, 2026, txt_path=path) == 4
    assert txt_loader.promote_rough(conn, 1, 2026, txt_path=path) == 0
    assert conn.execute("SELECT count(*) FROM v_rough_vs_final WHERE status='unchanged'").fetchone()[0] > 0
    conn.close()


XML_SAMPLE = b"""<Root><sr11><Report sub_report_title="U.S. Wheat Supply and Use"
    Report_Month="January 2026" page_title="WASDE - 670 - 11"
    sub_report_subtitle="(Million Metric Tons)"><matrix1><m1_attribute_group_Collection>
    <attribute_group><attribute attribute_name="Production"><year_group_Collection>
    <year_group market_year="2025/26"><month_group_Collection>
    <month_group forecast_month="1"><Cell cell_value="12.5" /></month_group>
    </month_group_Collection></year_group></year_group_Collection></attribute>
    </attribute_group></m1_attribute_group_Collection></matrix1></Report></sr11></Root>"""


def test_xml_excerpt_parser_and_idempotent_load(db_path, monkeypatch):
    root = ET.fromstring(XML_SAMPLE)
    assert xml_loader.parse_us_report(root.find("sr11/Report")) == [("Production", "United States", "2025/26", "1", "12.5")]
    monkeypatch.setattr(xml_loader, "download_xml", lambda month, year: XML_SAMPLE)
    conn = fresh_db(db_path)
    assert xml_loader.load_xml_report(conn, 1, 2026) == 1
    assert xml_loader.load_xml_report(conn, 1, 2026) == 0
    assert conn.execute("SELECT value FROM v_wasde WHERE report_title='U.S. Wheat Supply and Use'").fetchone()[0] == 12.5
    conn.close()


def downloader_module():
    path = Path(__file__).resolve().parents[1] / "backend" / "scripts" / "download_csv.py"
    spec = importlib.util.spec_from_file_location("download_csv_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_downloader_response_classes_and_existing_file(tmp_path, monkeypatch):
    script = downloader_module()

    class Response:
        status_code = 200
        content = b"one,two\n1,2\n"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(script.requests, "get", lambda *a, **kw: Response())
    assert script.download_file("https://example.invalid/data.csv") == (Response.content, 200)
    monkeypatch.setattr(script.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sys, "argv", ["download_csv.py", str(tmp_path), "2026", "2026", "1"])
    script.main()
    path = tmp_path / "oce-wasde-report-data-2026-01.csv"
    assert path.read_bytes() == Response.content
    monkeypatch.setattr(script.requests, "get", lambda *a, **kw: pytest.fail("existing file was fetched"))
    script.main()

    class EmptyResponse(Response):
        content = b""

    monkeypatch.setattr(script.requests, "get", lambda *a, **kw: EmptyResponse())
    assert script.download_file("x") == (None, "empty")
    monkeypatch.setattr(script.requests, "get", lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout("late")))
    assert script.download_file("x") == (None, "timeout")

    class MissingResponse(Response):
        status_code = 404

        def raise_for_status(self):
            error = requests.HTTPError("missing")
            error.response = self
            raise error

    monkeypatch.setattr(script.requests, "get", lambda *a, **kw: MissingResponse())
    assert script.download_file("x") == (None, "HTTP 404")


def test_cli_option_dispatch_and_rebuild_only_temporary_db(tmp_path, monkeypatch):
    path = tmp_path / "cli.db"
    with sqlite3.connect(path) as old:
        old.execute("CREATE TABLE stale(id INTEGER)")
    calls = []
    monkeypatch.setattr(main, "download_csvs", lambda: calls.append("download"))
    monkeypatch.setattr(main, "load_csv_files", lambda conn: calls.append("csv") or 2)
    for flag, name in (("load_prospective_plantings", "nass"), ("load_acreage_reports", "acreage"),
                       ("load_grain_stocks", "stocks"), ("load_crop_progress", "progress")):
        monkeypatch.setattr(main, flag, lambda conn, start_year, name=name: calls.append(name) or 1)
    monkeypatch.setattr(sys, "argv", ["main", "--db", str(path), "--rebuild", "--nass-start-year", "2020"])
    main.main()
    assert calls == ["download", "csv", "nass", "acreage", "stocks", "progress"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM units").fetchone()[0] > 0
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='stale'").fetchone() is None
    monkeypatch.setattr(sys, "argv", ["main", "--db", str(tmp_path / "unused.db")])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 1
    assert not (tmp_path / "unused.db").exists()


def test_cli_rough_promotion_and_rebuild_table_use_temporary_db(tmp_path, monkeypatch):
    path = tmp_path / "cli.db"
    calls = []
    monkeypatch.setattr(main, "load_txt_report", lambda conn, month, year, txt_path, target_table: calls.append(("rough", month, year, txt_path, target_table)) or 1)
    monkeypatch.setattr(main, "promote_rough", lambda conn, month, year, txt_path: calls.append(("promote", month, year, txt_path)) or 1)
    monkeypatch.setattr(sys, "argv", ["main", "--db", str(path), "--rough", "--promote-rough", "--txt-month", "1", "--txt-year", "2026", "--txt-file", "fixture.txt", "--rebuild-table", "load_log"])
    main.main()
    assert calls == [
        ("rough", 1, 2026, "fixture.txt", "wasde_data_rough"),
        ("promote", 1, 2026, "fixture.txt"),
    ]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM load_log").fetchone()[0] == 0
