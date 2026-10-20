# WASDE download integrity status

The previous verification proposal is now partly implemented for the CSV path:
validation before cache replacement, atomic cached-file writes, exact-byte SHA-256
tracking, archived accepted raw versions, and transactionally published CSV snapshots.
Use `--refresh-csv` to re-fetch existing files and detect remote corrections.

See [integrity changes and verification](../docs/INTEGRITY_FIXES.md) and
[the code/usage walkthrough](../docs/CODE_WALKTHROUGH.md).

A locally calculated SHA-256 is not an independently supplied USDA checksum. It
identifies the bytes accepted by this pipeline. No completeness certificate, automatic
remote polling, or NASS/TXT/XML versioning is claimed. HTTP 404s remain visible gaps.
