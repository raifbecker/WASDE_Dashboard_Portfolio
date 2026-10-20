CREATE VIEW v_latest_estimates AS
SELECT * FROM (
    SELECT v_wasde.*, ROW_NUMBER() OVER (
        PARTITION BY table_type_id, region_id, attribute, market_year, unit, annual_quarter_flag, reliability_projection ORDER BY report_date DESC
    ) AS recency
    FROM v_wasde
) WHERE recency = 1;
