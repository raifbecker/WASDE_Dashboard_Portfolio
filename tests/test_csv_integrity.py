"""Regression checks for record identity, atomic replacement and source corrections."""
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd
import pytest

from src import csv_loader, db
from tests.test_backend import downloader_module

pytestmark = pytest.mark.backend
BASE = dict(ReportDate='January 2026', WasdeNumber='670',
            ReportTitle='World Crop Supply and Use', Commodity='Corn', Region='World',
            Attribute='Production', MarketYear='2025/26', Unit='Million Metric Tons',
            Value='100', AnnualQuarterFlag='Annual', ReliabilityProjection='')


@pytest.fixture
def connection(db_path):
    with closing(db.get_connection(db_path)) as conn:
        db.create_tables(conn)
        yield conn


def publish(conn, rows, source='source.csv'):
    return csv_loader._load_dataframe(conn, pd.DataFrame(rows), source)


def test_dimensions_and_exact_duplicates_are_retained(connection):
    rows = [BASE, dict(BASE, AnnualQuarterFlag='I', Value='25'),
            dict(BASE, Unit='Metric Tons', Value='100000000'),
            dict(BASE, ReliabilityProjection='average', Value='2'),
            dict(BASE, ReliabilityProjection='largest', Value='5')]
    assert publish(connection, rows + [BASE]) == 5
    assert connection.execute('SELECT count(*) FROM wasde_data').fetchone()[0] == 5
    assert connection.execute('SELECT rows_loaded FROM load_log').fetchone()[0] == 5
    assert connection.execute('SELECT count(*) FROM v_dashboard').fetchone()[0] == 2


def test_correction_replaces_and_removes_only_its_own_rows(connection):
    assert publish(connection, [BASE, dict(BASE, Attribute='Exports', Value='10')]) == 2
    assert publish(connection, [dict(BASE, ReportDate='February 2026')], 'feb.csv') == 1
    assert publish(connection, [dict(BASE, Value='110')]) == 1
    assert connection.execute('SELECT value FROM wasde_data WHERE csv_source_file=?',
                              ('source.csv',)).fetchall() == [(110.0,)]
    assert connection.execute('SELECT count(*) FROM wasde_data').fetchone()[0] == 2
    assert connection.execute('SELECT count(*) FROM csv_versions').fetchone()[0] == 3
    assert publish(connection, [dict(BASE, Value='110')]) == 0
    assert connection.execute('SELECT count(*) FROM load_log').fetchone()[0] == 3


@pytest.mark.parametrize('rows', [
    [dict(BASE, Value='bad')], [dict(BASE, ReportDate='2026-99-01')],
    [dict(BASE, Commodity='')], [BASE, dict(BASE, Value='999')], [],
])
def test_invalid_replacement_preserves_last_good_snapshot(connection, rows):
    publish(connection, [BASE])
    digest = connection.execute('SELECT sha256 FROM csv_sources').fetchone()[0]
    with pytest.raises(ValueError):
        publish(connection, rows)
    assert connection.execute('SELECT value FROM wasde_data').fetchall() == [(100.0,)]
    assert connection.execute('SELECT sha256 FROM csv_sources').fetchone()[0] == digest
    assert connection.execute('SELECT count(*) FROM csv_versions').fetchone()[0] == 1


def test_database_failure_rolls_back_rows_dimensions_and_metadata(connection, monkeypatch):
    publish(connection, [BASE])
    original = csv_loader._insert_batch

    def fail_after_insert(conn, batch):
        original(conn, batch)
        raise sqlite3.OperationalError('simulated storage failure')

    monkeypatch.setattr(csv_loader, '_insert_batch', fail_after_insert)
    with pytest.raises(sqlite3.OperationalError):
        publish(connection, [dict(BASE, Commodity='New crop', Region='New region', Value='999')])
    assert connection.execute('SELECT value FROM wasde_data').fetchall() == [(100.0,)]
    assert connection.execute("SELECT count(*) FROM regions WHERE name='New Region'").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM table_types WHERE commodity='New crop'").fetchone()[0] == 0
    assert connection.execute('SELECT count(*) FROM csv_versions').fetchone()[0] == 1
    assert connection.execute('SELECT count(*) FROM load_log').fetchone()[0] == 1


def test_second_source_cannot_overwrite_another_sources_records(connection):
    publish(connection, [BASE])
    with pytest.raises(ValueError, match='another CSV'):
        publish(connection, [dict(BASE, Value='999')], 'other.csv')
    assert connection.execute('SELECT value FROM wasde_data').fetchall() == [(100.0,)]


def test_readers_see_old_snapshot_until_commit(connection, db_path, monkeypatch):
    publish(connection, [BASE])
    original = csv_loader._insert_batch
    with closing(sqlite3.connect(db_path)) as reader:
        def inspect_during_load(conn, batch):
            # The writer has deleted its old rows, but readers still see them.
            assert reader.execute('SELECT value FROM wasde_data').fetchall() == [(100.0,)]
            original(conn, batch)
            assert reader.execute('SELECT value FROM wasde_data').fetchall() == [(100.0,)]
        monkeypatch.setattr(csv_loader, '_insert_batch', inspect_during_load)
        publish(connection, [dict(BASE, Value='110')])
        assert reader.execute('SELECT value FROM wasde_data').fetchall() == [(110.0,)]


def test_metadata_failure_also_rolls_back_source_replacement(connection):
    publish(connection, [BASE])
    connection.execute("CREATE TRIGGER fail_log BEFORE INSERT ON load_log BEGIN SELECT RAISE(ABORT, 'simulated log failure'); END")
    connection.commit()
    with pytest.raises(sqlite3.IntegrityError, match='log failure'):
        publish(connection, [dict(BASE, Value='110')])
    assert connection.execute('SELECT value FROM wasde_data').fetchall() == [(100.0,)]
    assert connection.execute('SELECT count(*) FROM csv_versions').fetchone()[0] == 1
    assert connection.execute('SELECT count(*) FROM load_log').fetchone()[0] == 1


def test_replay_after_rebuilding_fact_table_restores_records(connection):
    publish(connection, [BASE])
    db.rebuild_table(connection, 'wasde_data')
    assert publish(connection, [BASE]) == 1
    assert connection.execute('SELECT value FROM wasde_data').fetchall() == [(100.0,)]
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='idx_wasde_unique'").fetchone()


def test_refresh_cli_uses_selected_directory(tmp_path, monkeypatch):
    from src import main
    calls = []
    monkeypatch.setattr(main, 'download_csvs', lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(main, 'load_csv_files', lambda conn, csv_dir: calls.append(csv_dir) or 1)
    monkeypatch.setattr(main.sys, 'argv', ['main', '--db', str(tmp_path / 'cli.db'),
                                         '--refresh-csv', '--csv-dir', str(tmp_path)])
    main.main()
    assert calls == [dict(refresh=True, csv_dir=tmp_path), tmp_path]


def test_revision_views_keep_commodity_unit_period_and_statistic_separate(connection):
    identities = [{}, dict(Commodity='Wheat'), dict(Unit='Metric Tons'),
                  dict(AnnualQuarterFlag='I'), dict(ReliabilityProjection='average')]
    rows = []
    for i, identity in enumerate(identities):
        rows.extend([dict(BASE, **identity, Value=str(100 + i * 100)),
                     dict(BASE, **identity, ReportDate='March 2026', Value=str(110 + i * 100))])
    publish(connection, rows)
    assert connection.execute('SELECT revision FROM v_revision_history WHERE previous_report_date IS NOT NULL').fetchall() == [(10.0,)] * 5
    assert connection.execute('SELECT change FROM v_month_over_month').fetchall() == [(10.0,)] * 5
    assert connection.execute('SELECT count(*) FROM v_latest_estimates').fetchone()[0] == 5
    # Schema setup must refresh views on existing databases too.
    db.create_tables(connection)
    assert connection.execute('SELECT change FROM v_month_over_month').fetchall() == [(10.0,)] * 5


def test_full_public_fixture_retains_every_observation(connection):
    path = Path(__file__).parent / 'fixtures/oce-wasde-report-data-2025-05.csv'
    content = path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == '4c08fdf3657a854d2100bb7dfead186fb1ea26be208e137c52619707cc060987'
    assert csv_loader.load_csv_files(connection, path.parent) == 4988
    assert connection.execute('SELECT count(*) FROM wasde_data').fetchone()[0] == 4988
    assert connection.execute('SELECT raw_content FROM csv_versions').fetchone()[0] == content
    assert connection.execute("SELECT count(*) FROM wasde_data WHERE annual_quarter_flag!='Annual'").fetchone()[0] == 120
    assert connection.execute("SELECT count(*) FROM wasde_data WHERE reliability_projection!=''").fetchone()[0] == 630
    assert csv_loader.load_csv_files(connection, path.parent) == 0
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_download_refresh_and_invalid_content_keep_existing_file(tmp_path, monkeypatch):
    script = downloader_module()
    monkeypatch.setattr(script, 'discover_report_urls', lambda: {})
    path = tmp_path / 'oce-wasde-report-data-2026-01.csv'
    original = pd.DataFrame([BASE]).to_csv(index=False).encode()
    corrected = pd.DataFrame([dict(BASE, Value='110')]).to_csv(index=False).encode()
    script.save_csv(original, path, '2026-01')
    with pytest.raises(ValueError):
        script.save_csv(b'<html>upstream error</html>', path, '2026-01')
    assert path.read_bytes() == original
    with pytest.raises(ValueError):
        script.save_csv(corrected, path, '2026-02')
    assert path.read_bytes() == original
    monkeypatch.setattr(script, 'download_file', lambda url: (corrected, 200))
    monkeypatch.setattr(script.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(script.sys, 'argv', ['download_csv.py', str(tmp_path), '2026', '2026', '1', '--refresh'])
    script.main()
    assert path.read_bytes() == corrected
    assert not list(tmp_path.glob('*.part'))


def test_archive_discovers_corrected_files_and_ignores_external_links():
    script = downloader_module()
    parser = script.ReportLinks()
    parser.feed('''<a href="/sites/default/files/documents/oce-wasde-report-data-2026-05-V2.csv">May</a>
        <a href="https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-2026-06-V2.csv">June</a>
        <a href="https://example.invalid/oce-wasde-report-data-2026-07.csv">external</a>''')
    assert set(parser.urls) == {'2026-05', '2026-06'}
    assert parser.urls['2026-05'].endswith('2026-05-V2.csv')


def test_known_versioned_links_survive_archive_access_failure(monkeypatch):
    import requests
    script = downloader_module()
    def blocked(*args, **kwargs):
        raise requests.ConnectionError('archive inaccessible')
    monkeypatch.setattr(script.requests, 'get', blocked)
    urls = script.discover_report_urls()
    assert urls['2026-05'].endswith('2026-05-V2.csv')
    assert urls['2026-06'].endswith('2026-06-V2.csv')


def test_downloader_uses_archive_url_but_keeps_stable_local_source_name(tmp_path, monkeypatch):
    script = downloader_module()
    url = 'https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-2026-01-V2.csv'
    monkeypatch.setattr(script, 'discover_report_urls', lambda: {'2026-01': url})
    requested = []
    content = pd.DataFrame([BASE]).to_csv(index=False).encode()
    monkeypatch.setattr(script, 'download_file', lambda address: (requested.append(address) or content, 200))
    monkeypatch.setattr(script.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(script.sys, 'argv', ['download_csv.py', str(tmp_path), '2026', '2026', '1'])
    script.main()
    assert requested == [url]
    assert (tmp_path / 'oce-wasde-report-data-2026-01.csv').read_bytes() == content


def test_old_schema_migrates_and_replay_repairs_legacy_csv_rows(tmp_path):
    with closing(db.get_connection(tmp_path / 'legacy.db')) as conn:
        for table in db.TABLE_ORDER:
            sql = db._read_sql('tables', table)
            if table.startswith('wasde_data'):
                sql = sql.replace(',\n    reliability_projection TEXT,\n    csv_source_file TEXT', '')
            conn.executescript(sql)
        title = db.get_or_create_table_type(conn, BASE['ReportTitle'], 'Corn', 'World')
        region = db.get_or_create_region(conn, 'World')
        conn.execute('INSERT INTO wasde_data(report_date,wasde_number,table_type_id,region_id,attribute,market_year,value,unit) VALUES (?,?,?,?,?,?,?,?)',
                     ('2026-01-01', 670, title, region, 'Production', '2025/26', 1, 'Million Metric Tons'))
        conn.execute("INSERT INTO load_log(source,source_file,rows_loaded) VALUES ('csv','oce-wasde-report-data-2026-01.csv',1)")
        conn.commit()
        db.create_tables(conn)
        assert publish(conn, [BASE, dict(BASE, AnnualQuarterFlag='I', Value='25')],
                       'oce-wasde-report-data-2026-01.csv') == 2
        assert conn.execute('SELECT value FROM wasde_data ORDER BY value').fetchall() == [(25.0,), (100.0,)]
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
