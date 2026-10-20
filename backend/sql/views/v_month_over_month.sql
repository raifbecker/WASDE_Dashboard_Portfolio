CREATE VIEW v_month_over_month AS
SELECT report_date, report_title, commodity, region, attribute, market_year,
       unit, annual_quarter_flag, reliability_projection,
       value AS current_value, previous_value, revision AS change,
       CASE WHEN previous_value != 0
            THEN ROUND(100.0 * revision / previous_value, 2) END AS pct_change,
       previous_report_date AS prev_report_date
FROM v_revision_history
WHERE previous_report_date IS NOT NULL;
