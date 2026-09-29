"""Small, disposable database shared by loader, API, and browser tests."""

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from src.db import create_tables, get_connection, get_or_create_region, get_or_create_table_type


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "wasde-test.db"


@pytest.fixture
def seeded_db(db_path):
    conn = get_connection(db_path)
    create_tables(conn)

    def add(title, commodity, scope, region, date, year, attribute, value, unit="Million Metric Tons"):
        table_type = get_or_create_table_type(conn, title, commodity, scope)
        region_id = get_or_create_region(conn, region)
        unit_row = conn.execute("SELECT id FROM units WHERE name = ?", (unit,)).fetchone()
        conn.execute(
            """INSERT INTO wasde_data
               (report_date, table_type_id, region_id, attribute, market_year, value, unit, unit_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (date, table_type, region_id, attribute, year, value, unit, unit_row[0]),
        )

    us = "U.S. Corn Supply and Use"
    world = "World Corn Supply and Use"
    for region, production in (("United States", 15), ("Iowa", 5)):
        add(us, "Corn", "U.S.", region, "2026-02-01", "2025/26", "Production", production)
    add(us, "Corn", "U.S.", "United States", "2026-02-01", "2025/26", "Exports", 2)

    for year, date, production, exports in (
        ("2025/26", "2026-01-01", 10, 2),
        ("2025/26", "2026-02-01", 7, 3),
        ("2025/26", "2026-03-01", 8, 4),
        ("2024/25", "2025-01-01", 0, 1),
        ("2024/25", "2025-02-01", 4, 2),
    ):
        add(world, "Corn", "World", "World", date, year, "Production", production)
        add(world, "Corn", "World", "World", date, year, "Exports", exports)
    add(world, "Corn", "World", "China", "2026-01-01", "2025/26", "Production", 4)
    add(world, "Corn", "World", "China", "2026-02-01", "2025/26", "Production", 5)
    add(world, "Corn", "World", "China", "2026-02-01", "2025/26", "Exports", None)
    add("World Wheat Supply and Use", "Wheat", "World", "World", "2026-02-01", "2025/26", "Production", 20)
    add("Other Corn Report", "Corn", "Other", "World", "2026-02-01", "2025/26", "Production", 1)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def dashboard(monkeypatch, seeded_db):
    from frontend import app as module
    monkeypatch.setattr(module, "DATABASE", str(seeded_db))
    module.app.config.update(TESTING=True)
    return module


@pytest.fixture
def client(dashboard):
    return dashboard.app.test_client()
