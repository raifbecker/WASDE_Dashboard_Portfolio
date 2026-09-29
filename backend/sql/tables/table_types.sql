CREATE TABLE IF NOT EXISTS table_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_title TEXT NOT NULL,
    commodity TEXT,
    scope TEXT,
    UNIQUE(report_title, commodity)
);
