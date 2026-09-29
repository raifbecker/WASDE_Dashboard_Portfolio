#!/usr/bin/env python3
"""
Sanitize the WASDE database:
  - Merge duplicate regions that differ only by capitalization or errant pluralization.
  - Re-point all foreign keys (wasde_data, wasde_data_rough) to the canonical region id.
  - Delete the orphaned duplicate region rows.
"""

import logging
import sys
from collections import defaultdict
from pathlib import Path

# Allow running as a standalone script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import DEFAULT_DB_PATH, get_connection, normalize_region_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Tables with a region_id FK column
FK_TABLES = ["wasde_data", "wasde_data_rough"]

# Attribute aliases: raw value -> canonical value
_ATTRIBUTE_ALIASES = {
    "Feed": "Domestic Feed",
}

# Commodity aliases: raw value -> canonical value
_COMMODITY_ALIASES = {
    "Coarse Grains": "Coarse Grain",
}


def sanitize_table_types(conn):
    """Merge (Contd.) table_types into their base titles and normalize commodity names."""
    fixed = 0

    # Merge (Contd.) rows into their base counterparts
    contd_rows = conn.execute(
        "SELECT id, report_title, commodity FROM table_types WHERE report_title LIKE '%(Contd.)%'"
    ).fetchall()
    for contd_id, contd_title, commodity in contd_rows:
        import re
        base_title = re.sub(r'\s*\(Contd\.\)', '', contd_title).strip()
        # Collapse any double spaces left over
        base_title = re.sub(r'  +', '  ', base_title)
        base = conn.execute(
            "SELECT id FROM table_types WHERE report_title = ? AND commodity IS NOT NULL AND id != ? LIMIT 1",
            (base_title, contd_id),
        ).fetchone()
        if not base:
            # Try matching without commodity filter
            base = conn.execute(
                "SELECT id FROM table_types WHERE report_title = ? AND id != ? LIMIT 1",
                (base_title, contd_id),
            ).fetchone()
        if base:
            base_id = base[0]
            for table in FK_TABLES:
                updated = conn.execute(
                    f"UPDATE {table} SET table_type_id = ? WHERE table_type_id = ?",
                    (base_id, contd_id),
                ).rowcount
                if updated:
                    logger.info(
                        "Remapped %d rows in %s from table_type %d (%s) -> %d (%s)",
                        updated, table, contd_id, contd_title, base_id, base_title,
                    )
            conn.execute("DELETE FROM table_types WHERE id = ?", (contd_id,))
            logger.info("Deleted (Contd.) table_type %d: %s", contd_id, contd_title)
            fixed += 1
        else:
            logger.warning("No base table_type found for %d: %s", contd_id, contd_title)

    # Normalize commodity names
    for raw, canonical in _COMMODITY_ALIASES.items():
        updated = conn.execute(
            "UPDATE table_types SET commodity = ? WHERE commodity = ?",
            (canonical, raw),
        ).rowcount
        if updated:
            logger.info("Renamed commodity %r -> %r (%d rows)", raw, canonical, updated)
            fixed += updated

    conn.commit()
    return fixed


def sanitize_attributes(conn):
    """Rename attribute values that are known aliases."""
    fixed = 0
    for raw, canonical in _ATTRIBUTE_ALIASES.items():
        for table in FK_TABLES:
            updated = conn.execute(
                f"UPDATE {table} SET attribute = ? WHERE attribute = ?",
                (canonical, raw),
            ).rowcount
            if updated:
                logger.info(
                    "Renamed attribute %r -> %r in %s (%d rows)",
                    raw, canonical, table, updated,
                )
                fixed += updated
    conn.commit()
    return fixed


def sanitize_regions(conn):
    """Merge region rows that normalize to the same name."""
    rows = conn.execute("SELECT id, name FROM regions ORDER BY id").fetchall()

    # Group region ids by their normalized name
    groups = defaultdict(list)
    for region_id, raw_name in rows:
        canonical = normalize_region_name(raw_name)
        groups[canonical].append((region_id, raw_name))

    merged = 0
    for canonical, entries in groups.items():
        if len(entries) == 1:
            # Only one id for this canonical name — just fix the spelling if needed
            region_id, raw_name = entries[0]
            if raw_name != canonical:
                logger.info("Renaming region %d: %r -> %r", region_id, raw_name, canonical)
                conn.execute(
                    "UPDATE regions SET name = ? WHERE id = ?",
                    (canonical, region_id),
                )
                merged += 1
            continue

        # Multiple ids map to the same canonical name — merge them
        # Keep the lowest id as the canonical one
        entries.sort(key=lambda e: e[0])
        keep_id, keep_name = entries[0]

        for dup_id, dup_name in entries[1:]:
            logger.info(
                "Merging region %d (%r) into %d (%r)",
                dup_id, dup_name, keep_id, canonical,
            )
            for table in FK_TABLES:
                updated = conn.execute(
                    f"UPDATE {table} SET region_id = ? WHERE region_id = ?",
                    (keep_id, dup_id),
                ).rowcount
                if updated:
                    logger.info("  %s: re-pointed %d rows", table, updated)

            conn.execute("DELETE FROM regions WHERE id = ?", (dup_id,))
            merged += 1

        # Rename the keeper after duplicates are gone (avoids UNIQUE conflicts)
        if keep_name != canonical:
            conn.execute(
                "UPDATE regions SET name = ? WHERE id = ?",
                (canonical, keep_id),
            )

    conn.commit()
    return merged


def report_regions(conn):
    """Print all region names after sanitization."""
    rows = conn.execute("SELECT id, name FROM regions ORDER BY name").fetchall()
    logger.info("Regions after sanitization (%d total):", len(rows))
    for region_id, name in rows:
        logger.info("  [%d] %s", region_id, name)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Sanitize WASDE database")
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without modifying the database",
    )
    args = parser.parse_args()

    conn = get_connection(args.db)

    if args.dry_run:
        logger.info("DRY RUN — no changes will be written")
        rows = conn.execute("SELECT id, name FROM regions ORDER BY id").fetchall()
        groups = defaultdict(list)
        for region_id, raw_name in rows:
            canonical = normalize_region_name(raw_name)
            groups[canonical].append((region_id, raw_name))

        issues = 0
        for canonical, entries in groups.items():
            if len(entries) > 1:
                logger.info("DUPLICATE: %r <- %s", canonical,
                            ", ".join(f"{rid} ({rn!r})" for rid, rn in entries))
                issues += 1
            elif entries[0][1] != canonical:
                logger.info("RENAME: %d %r -> %r", entries[0][0], entries[0][1], canonical)
                issues += 1

        if issues == 0:
            logger.info("No issues found.")
        else:
            logger.info("%d issue(s) found. Run without --dry-run to fix.", issues)
        conn.close()
        return

    merged = sanitize_regions(conn)
    if merged:
        logger.info("Sanitized %d region(s).", merged)
    else:
        logger.info("No region issues found.")

    tt_fixed = sanitize_table_types(conn)
    if tt_fixed:
        logger.info("Sanitized %d table_type(s).", tt_fixed)
    else:
        logger.info("No table_type issues found.")

    attr_fixed = sanitize_attributes(conn)
    if attr_fixed:
        logger.info("Sanitized %d attribute(s).", attr_fixed)
    else:
        logger.info("No attribute issues found.")

    report_regions(conn)
    conn.close()


if __name__ == "__main__":
    main()
