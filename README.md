# data-quality-validation

Validates pipeline output files against an Excel spec, one spec per pipeline.
Built to QC datasets produced by different ELT processes from the same source.

## What it checks
- Structure: missing, unexpected, or duplicate columns
- Key: nulls and duplicates at the declared grain
- Columns: nulls, type, allowed values, range, length, regex, uniqueness
- Hygiene: placeholder values, whitespace, case mismatches
- Business rules: cross-column rules from the spec

## Usage
```
pip install -r requirements.txt
python validate_data.py --pipeline NAME --spec specs/NAME_spec.xlsx \
  --data output.csv --key id_col,week --out qc_out
```
Supports CSV, TSV, Excel, Parquet, and SAS7BDAT. Exits nonzero if errors are found.

## Spec format
First sheet: one row per column (`column`, `dtype`, `nullable`, `allowed_values`,
`min`, `max`, `regex`, `date_format`, `unique`, `severity`). Separate allowed
values with `|`. Optional `business_rules` sheet: `rule_id`, `description`,
`violation_expr`, `severity`. See `specs/example_spec.xlsx`.

## Output
`qc_out/` gets a long-format errors file, a summary by check and column, and an
Excel workbook with a column profile.

## Data handling
Never commit data or QC outputs. They contain real row values.
