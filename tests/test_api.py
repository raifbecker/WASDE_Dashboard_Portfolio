import pytest


pytestmark = pytest.mark.api

WORLD = "World Corn Supply and Use"


def test_home_and_static_asset(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"WASDE Data Explorer" in response.data
    assert b"/static/vendor/" in response.data
    assert client.get("/static/vendor/jquery-3.7.1.min.js").status_code == 200


def test_report_types_and_scope_order(client):
    assert client.get("/api/report_types").json == {
        "report_types": ["Other Corn Report", "U.S. Corn Supply and Use", WORLD, "World Wheat Supply and Use"]
    }
    assert client.get("/api/report_types?scope=World").json == {
        "report_types": [WORLD, "World Wheat Supply and Use"]
    }
    assert client.get("/api/filters").json == {"scopes": ["U.S.", "World", "Other"]}
    assert client.get("/api/filters", query_string={"report_title": WORLD}).json == {"scopes": ["World"]}


def test_cascading_filter_routes(client):
    q = {"report_title": WORLD, "scope": "World", "commodity": "Corn"}
    assert client.get("/api/commodities", query_string=q).json == {"commodities": ["Corn"]}
    assert client.get("/api/commodities").json == {"commodities": []}
    assert client.get("/api/dates", query_string=q).json == {
        "dates": ["2026-03-01", "2026-02-01", "2026-01-01", "2025-02-01", "2025-01-01"]
    }
    assert client.get("/api/market_years", query_string={**q, "report_date": "2026-02-01"}).json == {
        "market_years": ["2025/26"]
    }
    assert client.get("/api/dates").json == {"dates": []}
    assert client.get("/api/market_years").json == {"market_years": []}


def test_regional_pivot_and_empty_result(client):
    q = {"report_title": WORLD, "commodity": "Corn", "scope": "World", "report_date": "2026-02-01", "market_year": "2025/26"}
    data = client.get("/api/table", query_string=q).json
    assert data["columns"] == ["China", "World"]
    rows = {r["attribute"]: r for r in data["rows"]}
    assert rows["Production"] == {"attribute": "Production", "unit": "Million Metric Tons", "China": 5.0, "World": 7.0}
    assert rows["Exports"]["World"] == 3.0
    assert rows["Exports"]["China"] is None
    assert client.get("/api/table").json == {"columns": [], "rows": []}
    assert client.get("/api/table", query_string={**q, "commodity": "Rice"}).json == {"columns": [], "rows": []}


def test_world_filter_and_time_table_routes(client):
    q = {"report_title": WORLD, "commodity": "Corn", "region": "World"}
    assert client.get("/api/world/regions", query_string=q).json == {"regions": ["China", "World"]}
    assert client.get("/api/world/attributes", query_string=q).json == {"attributes": ["Exports", "Production"]}
    assert client.get("/api/world/market_years", query_string=q).json == {"market_years": ["2025/26", "2024/25"]}
    data = client.get("/api/world/time_table", query_string={**q, "market_year": "2025/26"}).json
    assert data["columns"] == ["2026-03-01", "2026-02-01", "2026-01-01"]
    assert {r["attribute"]: r["2026-02-01"] for r in data["rows"]} == {"Exports": 3.0, "Production": 7.0}
    for endpoint, key in (("regions", "regions"), ("attributes", "attributes"), ("market_years", "market_years")):
        assert client.get(f"/api/world/{endpoint}").json == {key: []}
    assert client.get("/api/world/time_table").json == {"columns": [], "rows": []}
    assert client.get("/api/world/time_table", query_string={**q, "market_year": "missing"}).json == {"columns": [], "rows": []}


def test_world_chart_statistics_and_empty_result(client):
    q = {"report_title": WORLD, "commodity": "Corn", "region": "World", "attribute": "Production"}
    data = client.get("/api/world/chart", query_string=q).json
    assert [(s["market_year"], s["values"]) for s in data["series"]] == [
        ("2025/26", [10.0, 7.0, 8.0] + [None] * 9),
        ("2024/25", [0.0, 4.0] + [None] * 10),
    ]
    assert data["series"][0]["months"] == list(range(1, 13))
    assert data["series"][0]["report_dates"][:3] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert data["stats"] == {"mean_change": 1.0, "median_change": 1.0, "biggest_reduction": -2.0, "biggest_increase": 4.0}
    summaries = {r["market_year"]: r for r in data["year_summaries"]}
    assert summaries["2025/26"]["biggest_monthly_drop"] == -3.0
    assert summaries["2024/25"]["pct_change"] is None
    assert client.get("/api/world/chart").json == {"series": [], "stats": {}}
    assert client.get("/api/world/chart", query_string={**q, "attribute": "Unknown"}).json == {"series": [], "stats": {}}


def test_api_does_not_mix_quarters_statistics_or_units(client, seeded_db):
    import sqlite3
    with sqlite3.connect(seeded_db) as conn:
        # Copy an existing annual observation as a quarter and a reliability statistic.
        for period, statistic in [('I', ''), ('Annual', 'average')]:
            conn.execute('''INSERT INTO wasde_data
                (report_date,table_type_id,region_id,attribute,market_year,value,unit,
                 annual_quarter_flag,reliability_projection)
                SELECT report_date,table_type_id,region_id,attribute,market_year,999,unit,?,?
                FROM wasde_data WHERE attribute='Production' AND report_date='2026-02-01'
                AND region_id=(SELECT id FROM regions WHERE name='World')
                AND table_type_id=(SELECT id FROM table_types WHERE report_title=?) LIMIT 1''',
                (period, statistic, WORLD))
    q = dict(report_title=WORLD, commodity='Corn', scope='World', report_date='2026-02-01', market_year='2025/26')
    response = client.get('/api/table', query_string=q)
    assert response.status_code == 200
    assert {r['attribute']: r['World'] for r in response.json['rows']}['Production'] == 7
    with sqlite3.connect(seeded_db) as conn:
        conn.execute('''INSERT INTO wasde_data
            (report_date,table_type_id,region_id,attribute,market_year,value,unit)
            SELECT report_date,table_type_id,region_id,attribute,market_year,7000000,'Metric Tons'
            FROM wasde_data WHERE attribute='Production' AND report_date='2026-02-01'
            AND region_id=(SELECT id FROM regions WHERE name='World')
            AND table_type_id=(SELECT id FROM table_types WHERE report_title=?) LIMIT 1''', (WORLD,))
    assert client.get('/api/table', query_string=q).status_code == 400
    chart = client.get('/api/world/chart', query_string={**q, 'region':'World', 'attribute':'Production'})
    assert chart.status_code == 400
    assert 'multiple units' in chart.json['error']


@pytest.mark.parametrize('start_month', [3, 7, 11])
def test_chart_runs_forward_twelve_months_from_each_series_start(client, seeded_db, start_month):
    import sqlite3
    import pandas as pd
    start = pd.Period(f'2023-{start_month:02}', freq='M')
    with sqlite3.connect(seeded_db) as conn:
        template = conn.execute("SELECT table_type_id,region_id FROM v_dashboard WHERE report_title=? AND region='World' LIMIT 1", (WORLD,)).fetchone()
        for offset, value in [(0, 100), (2, 120), (11, 210), (12, 999)]:
            date = (start + offset).start_time.strftime('%Y-%m-%d')
            conn.execute("INSERT INTO wasde_data(report_date,table_type_id,region_id,attribute,market_year,value,unit) VALUES (?,?,?,?,?,?,?)", (date, *template, 'Production', 'test-window', value, 'Million Metric Tons'))
    q = dict(report_title=WORLD, commodity='Corn', region='World', attribute='Production')
    data = client.get('/api/world/chart', query_string=q).json
    series = next(s for s in data['series'] if s['market_year']=='test-window')
    assert series['months'] == [(start + i).month for i in range(12)]
    assert series['window_start'] == str(start)
    assert series['window_end'] == str(start + 11)
    assert series['values'] == [100, None, 120] + [None] * 8 + [210]
    assert series['report_dates'][1] is None
    assert series['report_dates'][-1] == (start + 11).start_time.strftime('%Y-%m-%d')
    summary = next(s for s in data['year_summaries'] if s['market_year']=='test-window')
    assert summary['last'] == 210
    assert summary['total_change'] == 110
