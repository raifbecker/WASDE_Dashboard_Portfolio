CREATE VIEW IF NOT EXISTS v_month_over_month AS
SELECT
    cur.report_date,
    cur.report_title,
    cur.commodity,
    cur.region,
    cur.attribute,
    cur.market_year,
    cur.value AS current_value,
    prev.value AS previous_value,
    cur.value - prev.value AS change,
    CASE WHEN prev.value != 0 THEN ROUND((cur.value - prev.value) / prev.value * 100, 2) ELSE NULL END AS pct_change,
    prev.report_date AS prev_report_date,
    cur.unit
FROM v_wasde cur
JOIN v_wasde prev
    ON cur.report_title = prev.report_title
    AND cur.region = prev.region
    AND cur.attribute = prev.attribute
    AND cur.market_year = prev.market_year
    AND prev.report_date = (
        SELECT MAX(p2.report_date)
        FROM v_wasde p2
        WHERE p2.report_title = cur.report_title
          AND p2.region = cur.region
          AND p2.attribute = cur.attribute
          AND p2.market_year = cur.market_year
          AND p2.report_date < cur.report_date
    );
