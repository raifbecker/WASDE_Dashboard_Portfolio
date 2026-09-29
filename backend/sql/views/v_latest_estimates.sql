CREATE VIEW IF NOT EXISTS v_latest_estimates AS
SELECT v.*
FROM v_wasde v
INNER JOIN (
    SELECT table_type_id, region_id, attribute, market_year, MAX(report_date) AS max_date
    FROM wasde_data
    GROUP BY table_type_id, region_id, attribute, market_year
) latest
ON v.report_date = latest.max_date
    AND v.report_title = (SELECT report_title FROM table_types WHERE id = latest.table_type_id)
    AND v.region = (SELECT name FROM regions WHERE id = latest.region_id)
    AND v.attribute = latest.attribute
    AND v.market_year = latest.market_year;
