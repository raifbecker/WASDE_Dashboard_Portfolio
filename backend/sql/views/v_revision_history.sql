-- A revision compares the same series in consecutive available reports.
CREATE VIEW v_revision_history AS
SELECT *, value - previous_value AS revision
FROM (
    SELECT v_wasde.*,
        LAG(value) OVER series AS previous_value,
        LAG(report_date) OVER series AS previous_report_date
    FROM v_wasde
    WINDOW series AS (
        PARTITION BY table_type_id, region_id, attribute, market_year, unit, annual_quarter_flag, reliability_projection
        ORDER BY report_date
    )
);
