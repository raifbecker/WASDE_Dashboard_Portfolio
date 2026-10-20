CREATE TABLE IF NOT EXISTS wasde_data_rough (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    wasde_number INTEGER,
    table_type_id INTEGER NOT NULL REFERENCES table_types(id),
    region_id INTEGER NOT NULL REFERENCES regions(id),
    attribute TEXT NOT NULL,
    market_year TEXT NOT NULL,
    proj_est_flag TEXT,
    annual_quarter_flag TEXT,
    value REAL,
    unit TEXT,
    unit_id INTEGER REFERENCES units(id),
    release_date TEXT,
    release_time TEXT,
    forecast_year INTEGER,
    forecast_month INTEGER,
    reliability_projection TEXT,
    csv_source_file TEXT
);

-- The complete observation index is created by db._migrate_observation_keys.


CREATE INDEX IF NOT EXISTS idx_wasde_rough_report_date ON wasde_data_rough(report_date);
CREATE INDEX IF NOT EXISTS idx_wasde_rough_commodity ON wasde_data_rough(table_type_id);
CREATE INDEX IF NOT EXISTS idx_wasde_rough_region ON wasde_data_rough(region_id);
