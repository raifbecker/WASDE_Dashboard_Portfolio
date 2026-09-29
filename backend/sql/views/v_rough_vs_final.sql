DROP VIEW IF EXISTS v_rough_vs_final;
CREATE VIEW v_rough_vs_final AS
SELECT
    r.report_date,
    r.wasde_number,
    t.report_title,
    t.commodity,
    t.scope,
    reg.name AS region,
    r.attribute,
    r.market_year,
    COALESCE(r.unit, f.unit) AS unit,
    r.value AS rough_value,
    f.value AS final_value,
    CASE
        WHEN r.value IS NOT NULL AND f.value IS NOT NULL
        THEN f.value - r.value
        ELSE NULL
    END AS difference,
    CASE
        WHEN r.value IS NOT NULL AND f.value IS NOT NULL AND r.value != 0
        THEN ROUND((f.value - r.value) / r.value * 100, 4)
        ELSE NULL
    END AS pct_change,
    CASE
        WHEN f.value IS NULL THEN 'rough_only'
        WHEN f.value = r.value THEN 'unchanged'
        ELSE 'revised'
    END AS status
FROM wasde_data_rough r
JOIN table_types t ON t.id = r.table_type_id
JOIN regions reg ON reg.id = r.region_id
LEFT JOIN wasde_data f
    ON f.report_date = r.report_date
    AND f.table_type_id = r.table_type_id
    AND f.region_id = r.region_id
    AND f.attribute = r.attribute
    AND f.market_year = r.market_year;
