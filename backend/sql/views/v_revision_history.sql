CREATE VIEW IF NOT EXISTS v_revision_history AS
SELECT
    report_title,
    commodity,
    region,
    attribute,
    market_year,
    report_date,
    wasde_number,
    value,
    unit,
    value - LAG(value) OVER (
        PARTITION BY report_title, region, attribute, market_year
        ORDER BY report_date
    ) AS revision
FROM v_wasde
ORDER BY report_title, region, attribute, market_year, report_date;
