# Public regression fixture

`oce-wasde-report-data-2025-05.csv` is a complete historical USDA WASDE CSV.

Source: https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-2025-05.csv

SHA-256: `4c08fdf3657a854d2100bb7dfead186fb1ea26be208e137c52619707cc060987`

Copied byte-for-byte from the existing local WASDE Lakehouse fixture. It contains
4,988 observations. Its bytes are verified by the regression test; this is a local
integrity check, not an independently signed USDA checksum. It is sample data,
not a current agricultural outlook. `.gitattributes` preserves its line endings.
