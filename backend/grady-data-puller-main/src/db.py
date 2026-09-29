import re
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "wasde.db"

# Words that should keep their exact casing when encountered (case-insensitive lookup)
_ABBREVIATIONS = {"U.S.", "EU-27", "EU-27+UK", "FSU-12", "UK"}
_ABBREV_UPPER = {a.upper(): a for a in _ABBREVIATIONS}

# Words to always lowercase when not the first word (standard title-case rules)
_LOWERCASE_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "in",
    "nor", "of", "on", "or", "so", "the", "to", "up", "yet", "&",
}

# Known errant plurals: plural word → correct singular form
_PLURAL_TO_SINGULAR = {
    "Exporters": "Exporter",
    "Importers": "Importer",
}


def _capitalize_word(word):
    """Capitalize a word, handling leading punctuation like '(' or quotes."""
    for i, ch in enumerate(word):
        if ch.isalpha():
            return word[:i] + word[i:].capitalize()
    return word


def normalize_region_name(name):
    """Normalize a region name: fix capitalization and errant pluralization."""
    if not name or name == "nan":
        return name

    # Title-case each word, preserving known abbreviations
    words = name.split()
    normalized = []
    for i, word in enumerate(words):
        upper = word.upper()
        if upper in _ABBREV_UPPER:
            normalized.append(_ABBREV_UPPER[upper])
        elif i > 0 and word.lower().strip("(-") in _LOWERCASE_WORDS:
            normalized.append(word.lower())
        else:
            # Handle hyphenated words: capitalize each part (On-farm → On-Farm)
            if "-" in word and not word.startswith("-"):
                parts = word.split("-")
                normalized.append("-".join(_capitalize_word(p) for p in parts))
            else:
                normalized.append(_capitalize_word(word))

    # Fix errant pluralization
    for i, word in enumerate(normalized):
        if word in _PLURAL_TO_SINGULAR:
            normalized[i] = _PLURAL_TO_SINGULAR[word]

    return " ".join(normalized)
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Tables must be created in dependency order
TABLE_ORDER = [
    "table_types",
    "regions",
    "units",
    "wasde_data",
    "wasde_data_rough",
    "load_log",
]

# Views must be created in dependency order (v_wasde first, others depend on it)
VIEW_ORDER = [
    "v_wasde",
    "v_latest_estimates",
    "v_month_over_month",
    "v_supply_use_balance",
    "v_revision_history",
    "v_world_with_hierarchy",
    "v_rough_vs_final",
]


def _read_sql(subdir, name):
    return (SQL_DIR / subdir / f"{name}.sql").read_text()


def get_connection(db_path=None):
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_tables(conn):
    for table in TABLE_ORDER:
        conn.executescript(_read_sql("tables", table))
    # Migrate old-format table_types rows where report_title = "Prospective Plantings - Corn"
    _migrate_table_types(conn)
    # Backfill unit_id for any rows with a unit string but no unit_id
    conn.execute("""
        UPDATE wasde_data
        SET unit_id = (SELECT u.id FROM units u WHERE u.name = wasde_data.unit)
        WHERE unit IS NOT NULL AND unit_id IS NULL
    """)
    conn.execute("""
        UPDATE wasde_data_rough
        SET unit_id = (SELECT u.id FROM units u WHERE u.name = wasde_data_rough.unit)
        WHERE unit IS NOT NULL AND unit_id IS NULL
    """)
    for view in VIEW_ORDER:
        conn.executescript(_read_sql("views", view))
    conn.commit()


def _migrate_table_types(conn):
    """Migrate table_types schema and split old 'Report - Commodity' rows."""
    # Check if the old UNIQUE(report_title) constraint is still in place
    schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='table_types'"
    ).fetchone()
    if schema and "report_title TEXT NOT NULL UNIQUE" in schema[0]:
        # Recreate table with new composite constraint
        # Temporarily disable FK checks so we can drop the old table
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("ALTER TABLE table_types RENAME TO table_types_old")
        conn.execute(
            "CREATE TABLE table_types (\n"
            "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
            "    report_title TEXT NOT NULL,\n"
            "    commodity TEXT,\n"
            "    scope TEXT,\n"
            "    UNIQUE(report_title, commodity)\n"
            ")"
        )
        conn.execute(
            "INSERT INTO table_types (id, report_title, commodity, scope) "
            "SELECT id, report_title, commodity, scope FROM table_types_old"
        )
        conn.execute("DROP TABLE table_types_old")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()

    # Split old 'Report - Commodity' rows into separate report_title + commodity
    rows = conn.execute(
        "SELECT id, report_title FROM table_types "
        "WHERE commodity IS NULL AND report_title LIKE '% - %'"
    ).fetchall()
    for old_id, old_title in rows:
        parts = old_title.split(" - ", 1)
        if len(parts) == 2:
            new_title, commodity = parts[0].strip(), parts[1].strip()
            # Check if a row with the new split values already exists
            existing = conn.execute(
                "SELECT id FROM table_types WHERE report_title = ? AND commodity = ?",
                (new_title, commodity),
            ).fetchone()
            if existing:
                # Remap any wasde_data referencing the old id to the existing one
                conn.execute(
                    "UPDATE wasde_data SET table_type_id = ? WHERE table_type_id = ?",
                    (existing[0], old_id),
                )
                conn.execute(
                    "UPDATE wasde_data_rough SET table_type_id = ? WHERE table_type_id = ?",
                    (existing[0], old_id),
                )
                conn.execute("DELETE FROM table_types WHERE id = ?", (old_id,))
            else:
                conn.execute(
                    "UPDATE table_types SET report_title = ?, commodity = ? WHERE id = ?",
                    (new_title, commodity, old_id),
                )
    conn.commit()


def get_or_create_table_type(conn, report_title, commodity=None, scope=None):
    row = conn.execute(
        "SELECT id FROM table_types WHERE report_title = ? AND commodity IS ?",
        (report_title, commodity),
    ).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO table_types (report_title, commodity, scope) VALUES (?, ?, ?)",
        (report_title, commodity, scope),
    )
    return cur.lastrowid


def get_or_create_region(conn, name, parent_id=None):
    name = normalize_region_name(name)
    row = conn.execute(
        "SELECT id FROM regions WHERE name = ?", (name,)
    ).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO regions (name, parent_id) VALUES (?, ?)",
        (name, parent_id),
    )
    return cur.lastrowid


def get_unit_id(conn, unit_name):
    if not unit_name or unit_name == 'nan':
        return None
    row = conn.execute(
        "SELECT id FROM units WHERE name = ?", (unit_name,)
    ).fetchone()
    return row[0] if row else None


def rebuild_table(conn, table_name):
    """Drop and recreate a single table by name."""
    if table_name not in TABLE_ORDER:
        raise ValueError(f"Unknown table: {table_name}. Valid tables: {TABLE_ORDER}")
    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    conn.executescript(_read_sql("tables", table_name))
    conn.commit()


def already_loaded(conn, source_file):
    row = conn.execute(
        "SELECT id FROM load_log WHERE source_file = ?", (source_file,)
    ).fetchone()
    return row is not None


def log_load(conn, source, source_file, rows_loaded):
    conn.execute(
        "INSERT INTO load_log (source, source_file, rows_loaded) VALUES (?, ?, ?)",
        (source, source_file, rows_loaded),
    )
    conn.commit()
