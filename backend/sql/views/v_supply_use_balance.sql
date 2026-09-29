CREATE VIEW IF NOT EXISTS v_supply_use_balance AS
SELECT
    report_date,
    wasde_number,
    report_title,
    commodity,
    region,
    market_year,
    unit,
    MAX(CASE WHEN attribute LIKE '%Beginning Stocks%' THEN value END) AS beginning_stocks,
    MAX(CASE WHEN attribute = 'Production' THEN value END) AS production,
    MAX(CASE WHEN attribute = 'Imports' THEN value END) AS imports,
    MAX(CASE WHEN attribute LIKE '%Total Supply%' THEN value END) AS total_supply,
    MAX(CASE WHEN attribute LIKE '%Domestic%Use%' OR attribute LIKE '%Dom. Consumption%' THEN value END) AS domestic_use,
    MAX(CASE WHEN attribute = 'Exports' THEN value END) AS exports,
    MAX(CASE WHEN attribute LIKE '%Ending Stocks%' THEN value END) AS ending_stocks
FROM v_wasde
GROUP BY report_date, wasde_number, report_title, commodity, region, market_year, unit;
