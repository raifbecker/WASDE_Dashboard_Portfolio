"""Real Chromium checks against the same disposable database used by API tests."""

import os
import sqlite3
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server


pytestmark = pytest.mark.browser

WORLD = "World Corn Supply and Use"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def dashboard_url(dashboard):
    server = make_server("127.0.0.1", 0, dashboard.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def page(dashboard_url):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".venv" / "playwright-browsers"))
    local_libraries = ROOT / ".venv" / "chromium-libs" / "usr" / "lib" / "x86_64-linux-gnu"
    if local_libraries.is_dir():
        os.environ["LD_LIBRARY_PATH"] = str(local_libraries) + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        external = []
        errors = []

        def restrict_network(route):
            if route.request.url.startswith(dashboard_url + "/"):
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()

        context.route("**/*", restrict_network)
        tab = context.new_page()
        tab.set_default_timeout(8000)
        tab.on("pageerror", lambda error: errors.append(str(error)))
        try:
            yield tab, dashboard_url, external, errors
        finally:
            context.close()
            browser.close()


def test_regional_filters_table_and_csv_export(page):
    tab, url, external, errors = page
    tab.goto(url)
    tab.locator("#sel-market-year").get_by_role("option").first.wait_for(state="attached")
    tab.locator("#pivot-table thead th").first.wait_for(state="attached")
    assert tab.locator("#pivot-table thead").inner_text().startswith("Attribute")

    tab.locator("#sel-report-type").select_option(WORLD)
    tab.locator("#sel-scope").get_by_role("option", name="World").wait_for(state="attached")
    assert tab.locator("#sel-scope").input_value() == "World"
    tab.locator("#sel-date").select_option("2026-02-01")
    tab.locator("#sel-market-year").get_by_role("option", name="2025/26").wait_for(state="attached")
    tab.locator("#pivot-table").get_by_text("China", exact=True).wait_for()
    assert "World" in tab.locator("#pivot-table thead").inner_text()
    assert "7" in tab.locator("#pivot-table tbody").inner_text()

    with tab.expect_download() as download_info:
        tab.locator("#pane-regional button", has_text="CSV").click()
    download = download_info.value
    assert download.suggested_filename == "wasde_regional.csv"
    csv_text = Path(download.path()).read_text()
    assert '"Attribute","Unit","China","World"' in csv_text
    assert '"Production","Million Metric Tons","5","7"' in csv_text
    assert external == []
    assert errors == []


def test_world_tab_chart_stats_date_order_and_arrows(page):
    tab, url, external, errors = page
    tab.goto(url)
    tab.locator("#tab-time").click()
    tab.locator("#sel2-report-type").get_by_role("option", name=WORLD).wait_for(state="attached")
    tab.locator("#sel2-report-type").select_option(WORLD)
    tab.locator("#sel2-region").get_by_role("option", name="World").wait_for(state="attached")
    tab.locator("#sel2-region").select_option("World")
    tab.locator("#sel2-attribute").get_by_role("option", name="Production").wait_for(state="attached")
    tab.locator("#sel2-attribute").select_option("Production")
    tab.locator("#sel2-market-year").get_by_role("option", name="2025/26").wait_for(state="attached")
    tab.locator("#sel2-market-year").select_option("2025/26")
    tab.locator("#stats-panel").wait_for(state="visible")
    tab.wait_for_function("Chart.getChart(document.getElementById('year-chart')) !== undefined")
    assert tab.evaluate("Chart.getChart(document.getElementById('year-chart')).data.datasets.length") == 2
    assert "Mean Change" in tab.locator("#stats-panel").inner_text()
    assert "1" in tab.locator("#stats-aggregate").inner_text()
    tab.locator("#time-table thead").get_by_text("2026-03-01").wait_for()
    headers = tab.locator("#time-table thead th").all_inner_texts()
    assert headers[2:5] == ["2026-03-01", "2026-02-01", "2026-01-01"]
    assert "▼" in tab.locator("#time-table tbody").inner_text()
    tab.locator("#btn-toggle-order").click()
    headers = tab.locator("#time-table thead th").all_inner_texts()
    assert headers[2:5] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert "▲" in tab.locator("#time-table tbody").inner_text()
    assert external == []
    assert errors == []


def test_empty_table_and_visible_api_error(page):
    tab, url, external, errors = page
    tab.route("**/api/table?**", lambda route: route.fulfill(json={"columns": [], "rows": []}))
    tab.goto(url)
    tab.locator("#pivot-table").get_by_text("No data for this selection").wait_for()
    tab.unroute("**/api/table?**")
    tab.route("**/api/report_types", lambda route: route.fulfill(status=503, body="unavailable"))
    tab.reload()
    tab.locator("#app-error").wait_for(state="visible")
    assert external == []
    assert errors == []


def test_chart_preserves_cross_year_window_and_actual_tooltip_dates(page, seeded_db):
    with sqlite3.connect(seeded_db) as conn:
        template = conn.execute("SELECT table_type_id,region_id FROM v_dashboard WHERE report_title=? AND region='World' LIMIT 1", (WORLD,)).fetchone()
        for date, value in [('2025-05-01', 30), ('2026-04-01', 20), ('2026-05-01', 999)]:
            conn.execute("INSERT INTO wasde_data(report_date,table_type_id,region_id,attribute,market_year,value,unit) VALUES (?,?,?,?,?,?,?)",
                         (date, *template, 'Production', '2025/26', value, 'Million Metric Tons'))
    tab, url, external, errors = page
    tab.goto(url)
    tab.locator('#tab-time').click()
    tab.locator('#sel2-report-type').get_by_role('option', name=WORLD).wait_for(state='attached')
    tab.locator('#sel2-report-type').select_option(WORLD)
    tab.locator('#sel2-region').get_by_role('option', name='World').wait_for(state='attached')
    tab.locator('#sel2-region').select_option('World')
    tab.locator('#sel2-attribute').get_by_role('option', name='Production').wait_for(state='attached')
    tab.locator('#sel2-attribute').select_option('Production')
    tab.wait_for_function("""() => {
        const chart = Chart.getChart(document.getElementById('year-chart'));
        return chart && chart.data.datasets.some(s => s.label === '2025/26 (May 2025 – Apr 2026)' && s.data[11] === 20);
    }""")
    result = tab.evaluate("""() => {
        const chart = Chart.getChart(document.getElementById('year-chart'));
        const index = chart.data.datasets.findIndex(s => s.label.startsWith('2025/26'));
        return {
            labels: chart.data.labels,
            values: chart.data.datasets[index].data,
            spanGaps: chart.data.datasets[index].spanGaps,
            tooltip: chart.options.plugins.tooltip.callbacks.label({datasetIndex: index, dataIndex: 8, raw: 10}),
        };
    }""")
    assert result['labels'] == [f'Month {i}' for i in range(1, 13)]
    assert result['values'][0] == 30
    assert result['values'][1] is None
    assert result['values'][8:] == [10, 7, 8, 20]
    assert 999 not in result['values']
    assert result['spanGaps'] is False
    assert '2026-01-01' in result['tooltip']
    assert external == []
    assert errors == []
