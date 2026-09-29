CREATE TABLE IF NOT EXISTS load_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_file TEXT NOT NULL,
    loaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    rows_loaded INTEGER NOT NULL DEFAULT 0
);
