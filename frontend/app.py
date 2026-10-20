import json
import os
import sqlite3

import pandas as pd
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


@app.errorhandler(ValueError)
def invalid_series(error):
    return jsonify(error=str(error)), 400


def check_units(df):
    """A chart or pivot must never silently pick between incompatible units."""
    if not df.empty and (df.groupby('attribute')['unit'].nunique(dropna=False) > 1).any():
        raise ValueError('This selection contains multiple units for one measure. Use SQL to select a single unit.')

_settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
with open(_settings_path, "r") as _f:
    _settings = json.load(_f)

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), _settings["database_path"])


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# ── Main page ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Filter endpoints (cascading) ────────────────────────────────────────────

@app.route("/api/report_types")
def api_report_types():
    """Return distinct report_title values, excluding those with no scope."""
    scope = request.args.get("scope")
    db = get_db()
    try:
        query = (
            "SELECT DISTINCT report_title FROM v_dashboard "
            "WHERE report_title IS NOT NULL AND scope IS NOT NULL"
        )
        params = ()
        if scope:
            query += " AND scope = ?"
            params = (scope,)
        rows = db.execute(query + " ORDER BY report_title", params).fetchall()
        return jsonify(report_types=[r[0] for r in rows])
    finally:
        db.close()


@app.route("/api/filters")
def api_filters():
    """Return scopes, optionally filtered by report_title."""
    report_title = request.args.get("report_title")
    db = get_db()
    try:
        if report_title:
            raw = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT scope FROM v_dashboard "
                    "WHERE scope IS NOT NULL AND report_title = ? ORDER BY scope",
                    (report_title,),
                ).fetchall()
            ]
        else:
            raw = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT scope FROM v_dashboard WHERE scope IS NOT NULL ORDER BY scope"
                ).fetchall()
            ]
        # Pin U.S. first, World second, then any others alphabetically
        preferred = ["U.S.", "World"]
        scopes = [s for s in preferred if s in raw] + [
            s for s in raw if s not in preferred
        ]
        return jsonify(scopes=scopes)
    finally:
        db.close()


@app.route("/api/commodities")
def api_commodities():
    """Return commodities that have data for the given scope, optionally filtered by report_title."""
    scope = request.args.get("scope")
    report_title = request.args.get("report_title")
    if not scope:
        return jsonify(commodities=[])

    db = get_db()
    try:
        if report_title:
            rows = db.execute(
                "SELECT DISTINCT commodity FROM v_dashboard "
                "WHERE scope = ? AND report_title = ? ORDER BY commodity",
                (scope, report_title),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT DISTINCT commodity FROM v_dashboard "
                "WHERE scope = ? ORDER BY commodity",
                (scope,),
            ).fetchall()
        return jsonify(commodities=[r[0] for r in rows])
    finally:
        db.close()


@app.route("/api/dates")
def api_dates():
    """Return report dates for a report type, commodity, and scope."""
    report_title = request.args.get("report_title")
    commodity = request.args.get("commodity")
    scope = request.args.get("scope")
    if not report_title or not commodity or not scope:
        return jsonify(dates=[])

    db = get_db()
    try:
        rows = db.execute(
            "SELECT DISTINCT report_date FROM v_dashboard "
            "WHERE report_title = ? AND commodity = ? AND scope = ? "
            "ORDER BY report_date DESC",
            (report_title, commodity, scope),
        ).fetchall()
        return jsonify(dates=[r[0] for r in rows])
    finally:
        db.close()


@app.route("/api/market_years")
def api_market_years():
    """Return market years for a report type, commodity, scope, and date."""
    report_title = request.args.get("report_title")
    commodity = request.args.get("commodity")
    scope = request.args.get("scope")
    report_date = request.args.get("report_date")
    if not report_title or not commodity or not scope or not report_date:
        return jsonify(market_years=[])

    db = get_db()
    try:
        rows = db.execute(
            "SELECT DISTINCT market_year FROM v_dashboard "
            "WHERE report_title = ? AND commodity = ? AND scope = ? AND report_date = ? "
            "ORDER BY market_year DESC",
            (report_title, commodity, scope, report_date),
        ).fetchall()
        return jsonify(market_years=[r[0] for r in rows])
    finally:
        db.close()


# ── Pivot table data ────────────────────────────────────────────────────────

@app.route("/api/table")
def api_table():
    """Return pivoted table data: attributes (rows) × regions (columns)."""
    report_title = request.args.get("report_title")
    commodity = request.args.get("commodity")
    scope = request.args.get("scope")
    report_date = request.args.get("report_date")
    market_year = request.args.get("market_year")

    if not all([report_title, commodity, scope, report_date, market_year]):
        return jsonify(columns=[], rows=[])

    db = get_db()
    try:
        df = pd.read_sql_query(
            "SELECT attribute, region, value, unit "
            "FROM v_dashboard "
            "WHERE report_title = ? AND commodity = ? AND scope = ? "
            "AND report_date = ? AND market_year = ?",
            db,
            params=(report_title, commodity, scope, report_date, market_year),
        )
    finally:
        db.close()

    if df.empty:
        return jsonify(columns=[], rows=[])

    # Validate units before making a display label.
    check_units(df)
    unit_map = df.dropna(subset=["unit"]).drop_duplicates("attribute").set_index("attribute")["unit"].to_dict()

    # Pivot: attribute rows × region columns
    pivot = df.pivot_table(
        index="attribute", columns="region", values="value", aggfunc="first"
    )

    # Sort regions alphabetically
    pivot = pivot.reindex(sorted(pivot.columns), axis=1)

    regions = list(pivot.columns)

    rows = []
    for attr in pivot.index:
        row = {
            "attribute": attr,
            "unit": unit_map.get(attr, ""),
        }
        for region in regions:
            val = pivot.at[attr, region]
            row[region] = None if pd.isna(val) else val
        rows.append(row)

    return jsonify(columns=regions, rows=rows)


# ── Time comparison tab (World only) ───────────────────────────────────────

@app.route("/api/world/regions")
def api_world_regions():
    """Return distinct regions for a commodity in World scope."""
    report_title = request.args.get("report_title")
    commodity = request.args.get("commodity")
    if not report_title or not commodity:
        return jsonify(regions=[])

    db = get_db()
    try:
        rows = db.execute(
            "SELECT DISTINCT region FROM v_dashboard "
            "WHERE report_title = ? AND commodity = ? AND scope = 'World' "
            "ORDER BY region",
            (report_title, commodity),
        ).fetchall()
        return jsonify(regions=[r[0] for r in rows])
    finally:
        db.close()


@app.route("/api/world/market_years")
def api_world_market_years():
    """Return distinct market_years for commodity + region in World scope."""
    report_title = request.args.get("report_title")
    commodity = request.args.get("commodity")
    region = request.args.get("region")
    if not report_title or not commodity or not region:
        return jsonify(market_years=[])

    db = get_db()
    try:
        rows = db.execute(
            "SELECT DISTINCT market_year FROM v_dashboard "
            "WHERE report_title = ? AND commodity = ? AND scope = 'World' AND region = ? "
            "ORDER BY market_year DESC",
            (report_title, commodity, region),
        ).fetchall()
        return jsonify(market_years=[r[0] for r in rows])
    finally:
        db.close()


@app.route("/api/world/time_table")
def api_world_time_table():
    """Return pivot: attributes (rows) × report_dates (columns) for one region."""
    report_title = request.args.get("report_title")
    commodity = request.args.get("commodity")
    region = request.args.get("region")
    market_year = request.args.get("market_year")

    if not all([report_title, commodity, region, market_year]):
        return jsonify(columns=[], rows=[])

    db = get_db()
    try:
        df = pd.read_sql_query(
            "SELECT attribute, report_date, value, unit "
            "FROM v_dashboard "
            "WHERE report_title = ? AND commodity = ? AND scope = 'World' "
            "AND region = ? AND market_year = ?",
            db,
            params=(report_title, commodity, region, market_year),
        )
    finally:
        db.close()

    if df.empty:
        return jsonify(columns=[], rows=[])

    check_units(df)
    unit_map = df.dropna(subset=["unit"]).drop_duplicates("attribute").set_index("attribute")["unit"].to_dict()

    pivot = df.pivot_table(
        index="attribute", columns="report_date", values="value", aggfunc="first"
    )

    # Sort dates reverse-chronologically (latest on left)
    pivot = pivot.reindex(sorted(pivot.columns, reverse=True), axis=1)

    dates = list(pivot.columns)

    rows = []
    for attr in pivot.index:
        row = {"attribute": attr, "unit": unit_map.get(attr, "")}
        for d in dates:
            val = pivot.at[attr, d]
            row[d] = None if pd.isna(val) else val
        rows.append(row)

    return jsonify(columns=dates, rows=rows)


@app.route("/api/world/attributes")
def api_world_attributes():
    """Return distinct attributes for commodity + region in World scope."""
    report_title = request.args.get("report_title")
    commodity = request.args.get("commodity")
    region = request.args.get("region")
    if not report_title or not commodity or not region:
        return jsonify(attributes=[])

    db = get_db()
    try:
        rows = db.execute(
            "SELECT DISTINCT attribute FROM v_dashboard "
            "WHERE report_title = ? AND commodity = ? AND scope = 'World' AND region = ? "
            "ORDER BY attribute",
            (report_title, commodity, region),
        ).fetchall()
        return jsonify(attributes=[r[0] for r in rows])
    finally:
        db.close()


@app.route("/api/world/chart")
def api_world_chart():
    """Compare each market year's first available 12-month report window."""
    report_title = request.args.get("report_title")
    commodity = request.args.get("commodity")
    region = request.args.get("region")
    attribute = request.args.get("attribute")

    if not all([report_title, commodity, region, attribute]):
        return jsonify(series=[], stats={})

    db = get_db()
    try:
        df = pd.read_sql_query(
            "SELECT market_year, report_date, value, unit, attribute "
            "FROM v_dashboard "
            "WHERE report_title = ? AND commodity = ? AND scope = 'World' "
            "AND region = ? AND attribute = ?",
            db,
            params=(report_title, commodity, region, attribute),
        )
    finally:
        db.close()

    if df.empty:
        return jsonify(series=[], stats={})

    check_units(df)
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["report_month"] = df["report_date"].dt.to_period("M")

    # Build one series per market year
    series = []
    year_chronological = {}  # market_year -> values sorted by report_date
    for my, grp in df.groupby("market_year"):
        # Keep the calendar year when grouping. July 2025 and July 2026
        # are different months; the latter is outside this 12-month window.
        start = grp["report_month"].min()
        window = pd.period_range(start, periods=12, freq="M")
        monthly = (grp.sort_values("report_date")
                   .drop_duplicates("report_month", keep="last")
                   .set_index("report_month").reindex(window))
        values = [None if pd.isna(value) else float(value) for value in monthly["value"]]
        series.append({
            "market_year": my,
            "months": [month.month for month in window],
            "labels": [month.strftime("%b %Y") for month in window],
            "report_dates": [None if pd.isna(date) else date.strftime("%Y-%m-%d")
                             for date in monthly["report_date"]],
            "window_start": str(window[0]),
            "window_end": str(window[-1]),
            "values": values,
        })
        # Summaries must describe the same window as the chart.
        year_chronological[my] = [value for value in values if value is not None]

    # Sort series by market_year descending (most recent first)
    series.sort(key=lambda s: s["market_year"], reverse=True)

    # Compute first→last changes per year and statistics (using chronological order)
    all_changes = []
    year_summaries = []
    for s in series:
        vals = year_chronological.get(s["market_year"], [])
        if len(vals) >= 2:
            first_val = vals[0]
            last_val = vals[-1]
            total_change = last_val - first_val
            pct_change = (total_change / first_val * 100) if first_val != 0 else None
            # Biggest single-month drop
            month_changes = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
            biggest_drop = min(month_changes) if month_changes else 0
            all_changes.append(total_change)
            year_summaries.append({
                "market_year": s["market_year"],
                "first": first_val,
                "last": last_val,
                "total_change": round(total_change, 2),
                "pct_change": round(pct_change, 2) if pct_change is not None else None,
                "biggest_monthly_drop": round(biggest_drop, 2),
            })

    # Aggregate stats across all years
    stats = {}
    if all_changes:
        stats["mean_change"] = round(float(pd.Series(all_changes).mean()), 2)
        stats["median_change"] = round(float(pd.Series(all_changes).median()), 2)
        stats["biggest_reduction"] = round(float(min(all_changes)), 2)
        stats["biggest_increase"] = round(float(max(all_changes)), 2)

    return jsonify(series=series, stats=stats, year_summaries=year_summaries)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
