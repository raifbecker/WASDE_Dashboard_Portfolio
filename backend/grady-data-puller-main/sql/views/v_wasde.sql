DROP VIEW IF EXISTS v_wasde;
CREATE VIEW v_wasde AS
SELECT
    d.id,
    d.report_date,
    d.wasde_number,
    d.market_year,
    d.attribute,
    d.value,
    d.unit,
    d.proj_est_flag,
    d.annual_quarter_flag,
    d.forecast_year,
    d.forecast_month,
    t.report_title,
    t.commodity,
    t.scope,
    r.name AS region,
    pr.name AS parent_region,
    u.category AS unit_category,
    u.base_unit,
    u.multiplier AS unit_multiplier,
    CASE WHEN u.multiplier IS NOT NULL THEN d.value * u.multiplier ELSE NULL END AS base_value,
    'final' AS data_source
FROM wasde_data d
JOIN table_types t ON d.table_type_id = t.id
JOIN regions r ON d.region_id = r.id
LEFT JOIN regions pr ON r.parent_id = pr.id
LEFT JOIN units u ON d.unit_id = u.id
UNION ALL
SELECT
    d.id,
    d.report_date,
    d.wasde_number,
    d.market_year,
    d.attribute,
    d.value,
    d.unit,
    d.proj_est_flag,
    d.annual_quarter_flag,
    d.forecast_year,
    d.forecast_month,
    t.report_title,
    t.commodity,
    t.scope,
    r.name AS region,
    pr.name AS parent_region,
    u.category AS unit_category,
    u.base_unit,
    u.multiplier AS unit_multiplier,
    CASE WHEN u.multiplier IS NOT NULL THEN d.value * u.multiplier ELSE NULL END AS base_value,
    'rough' AS data_source
FROM wasde_data_rough d
JOIN table_types t ON d.table_type_id = t.id
JOIN regions r ON d.region_id = r.id
LEFT JOIN regions pr ON r.parent_id = pr.id
LEFT JOIN units u ON d.unit_id = u.id
WHERE NOT EXISTS (
    SELECT 1 FROM wasde_data m
    WHERE m.report_date = d.report_date
      AND m.table_type_id = d.table_type_id
      AND m.region_id = d.region_id
      AND m.attribute = d.attribute
      AND m.market_year = d.market_year
)

order BY report_date
;
