CREATE TABLE IF NOT EXISTS wasde_data (
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
    forecast_month INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wasde_unique
    ON wasde_data(report_date, table_type_id, region_id, attribute, market_year);

CREATE INDEX IF NOT EXISTS idx_wasde_report_date ON wasde_data(report_date);
CREATE INDEX IF NOT EXISTS idx_wasde_commodity ON wasde_data(table_type_id);
CREATE INDEX IF NOT EXISTS idx_wasde_region ON wasde_data(region_id);
