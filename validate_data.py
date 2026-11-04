#!/usr/bin/env python3
"""
Spec-driven data validation for pipeline outputs (OCHIN, Peter, or any other).

One engine, one spec per pipeline. The Excel spec is the single source of truth.

Usage
-----
python validate_data.py --pipeline OCHIN --spec ochin_spec.xlsx --data ochin_output.csv \
       --key member_id,week --out qc_out

python validate_data.py --pipeline PETER --spec peter_spec.xlsx --data peter_output.sas7bdat \
       --key member_id,week --out qc_out

Spec sheet (first sheet, or --sheet). One row per expected column. Headers are
matched loosely (case/space/underscore-insensitive); only `column` is required:

  column | dtype | nullable | allowed_values | min | max | min_length | max_length
  | regex | date_format | unique | severity | notes

  dtype          : string, int, float, date, datetime, bool   (default string)
  nullable       : Y/N, TRUE/FALSE, yes/no  (default Y)
  allowed_values : list separated by | ; or newline (comma only as a last resort)
  min / max      : numeric or date bound (for int/float/date/datetime)
  regex          : full-match pattern for the raw string value
  unique         : Y/N
  severity       : error | warn  (default error)

Optional second sheet named `business_rules` (cross-column rules):

  rule_id | description | violation_expr | severity

  violation_expr is a pandas .eval/.query expression that is TRUE for violating rows,
  evaluated on the TYPED frame, e.g.  `start_date > end_date`
  or `status == "Active" and coach_id.isna()`.

Outputs (in --out): <PIPELINE>_errors.csv (long format), <PIPELINE>_summary.csv,
<PIPELINE>_qc.xlsx (both tabs + spec issues).
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

NULL_TOKENS = {"", "NA", "N/A", "NULL", "NONE", "NAN", "NaN", "null", "None", "na", "n/a"}
PLACEHOLDER_TOKENS = {"UNKNOWN", "UNK", "TBD", "MISSING", "-", "--", "?"}
TRUE_TOKENS = {"1", "true", "t", "y", "yes"}
FALSE_TOKENS = {"0", "false", "f", "n", "no"}

HEADER_ALIASES = {
    "column": {"column", "columnname", "field", "fieldname", "variable", "name"},
    "dtype": {"dtype", "type", "datatype"},
    "nullable": {"nullable", "allownull", "allownulls", "optional", "null"},
    "required": {"required"},
    "allowed_values": {"allowedvalues", "expectedvalues", "values", "validvalues", "domain", "expectedvalue"},
    "min": {"min", "minimum", "minvalue"},
    "max": {"max", "maximum", "maxvalue"},
    "min_length": {"minlength"},
    "max_length": {"maxlength", "length", "maxlen"},
    "regex": {"regex", "pattern", "format"},
    "date_format": {"dateformat"},
    "unique": {"unique", "isunique"},
    "severity": {"severity", "level"},
    "notes": {"notes", "rulenotes", "description", "comment", "comments"},
}


# ----------------------------------------------------------------------------
# Spec loading
# ----------------------------------------------------------------------------
def _norm(h):
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


def _truthy(v, default):
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return default
    return str(v).strip().lower() in {"y", "yes", "true", "t", "1", "x"}


def _blank(v):
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""


def _split_values(v):
    if _blank(v):
        return None
    s = str(v)
    for sep in ("|", ";", "\n"):
        if sep in s:
            return [x.strip() for x in s.split(sep) if x.strip() != ""]
    if "," in s:
        return [x.strip() for x in s.split(",") if x.strip() != ""]
    return [s.strip()]


def load_spec(path, sheet=None):
    raw = pd.read_excel(path, sheet_name=sheet or 0, dtype=object)
    mapping = {}
    for col in raw.columns:
        n = _norm(col)
        for canon, aliases in HEADER_ALIASES.items():
            if n in aliases and canon not in mapping.values():
                mapping[col] = canon
                break
    spec = raw.rename(columns=mapping)
    if "column" not in spec.columns:
        sys.exit(f"Spec must have a column-name header. Found headers: {list(raw.columns)}")
    spec = spec[spec["column"].notna()].copy()
    spec["column"] = spec["column"].astype(str).str.strip()

    rules = []
    for _, r in spec.iterrows():
        nullable = _truthy(r.get("nullable"), True)
        if "required" in spec.columns and not _blank(r.get("required")):
            nullable = not _truthy(r.get("required"), False)
        rules.append({
            "column": r["column"],
            "dtype": (str(r.get("dtype")).strip().lower() if not _blank(r.get("dtype")) else "string"),
            "nullable": nullable,
            "allowed": _split_values(r.get("allowed_values")),
            "min": None if _blank(r.get("min")) else r.get("min"),
            "max": None if _blank(r.get("max")) else r.get("max"),
            "min_length": None if _blank(r.get("min_length")) else int(float(r.get("min_length"))),
            "max_length": None if _blank(r.get("max_length")) else int(float(r.get("max_length"))),
            "regex": None if _blank(r.get("regex")) else str(r.get("regex")).strip(),
            "date_format": None if _blank(r.get("date_format")) else str(r.get("date_format")).strip(),
            "unique": _truthy(r.get("unique"), False),
            "severity": (str(r.get("severity")).strip().lower() if not _blank(r.get("severity")) else "error"),
        })

    biz = None
    try:
        b = pd.read_excel(path, sheet_name="business_rules", dtype=object)
        b.columns = [_norm(c) for c in b.columns]
        biz = b.rename(columns={"ruleid": "rule_id", "violationexpr": "violation_expr"})
        biz = biz[biz["violation_expr"].notna()]
    except Exception:
        pass
    return rules, biz


# ----------------------------------------------------------------------------
# Data loading (everything as raw strings; typing is part of validation)
# ----------------------------------------------------------------------------
def load_data(path):
    p = Path(path)
    ext = p.suffix.lower()
    if ext in {".csv", ".txt"}:
        df = pd.read_csv(p, dtype=str, keep_default_na=False, na_filter=False)
    elif ext == ".tsv":
        df = pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False, na_filter=False)
    elif ext in {".xlsx", ".xls"}:
        df = pd.read_excel(p, dtype=str, keep_default_na=False, na_filter=False)
    elif ext == ".parquet":
        df = pd.read_parquet(p).astype(object).where(lambda x: x.notna(), "").astype(str)
    elif ext == ".sas7bdat":
        df = pd.read_sas(p, format="sas7bdat", encoding="utf-8")
        df = df.astype(object).where(df.notna(), "").astype(str)
    else:
        sys.exit(f"Unsupported file type: {ext}")
    return df


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
class ErrorLog:
    def __init__(self, pipeline, df, key_cols):
        self.pipeline = pipeline
        self.df = df
        self.key_cols = [k for k in key_cols if k in df.columns]
        self.rows = []

    def add(self, mask, check, column, severity, detail="", values=None):
        """mask: boolean Series aligned to df. values: Series of bad values (default raw column)."""
        idx = mask[mask].index
        if len(idx) == 0:
            return
        vals = values if values is not None else (self.df[column] if column in self.df.columns else None)
        for i in idx:
            key = "|".join(str(self.df.at[i, k]) for k in self.key_cols) if self.key_cols else ""
            self.rows.append({
                "pipeline": self.pipeline,
                "check": check,
                "column": column,
                "source_row": int(i) + 2,  # +2 = header + 1-based, matches CSV line numbers
                "row_key": key,
                "bad_value": None if vals is None else vals.at[i],
                "severity": severity,
                "detail": detail,
            })

    def add_dataset(self, check, column, severity, detail):
        self.rows.append({"pipeline": self.pipeline, "check": check, "column": column,
                          "source_row": None, "row_key": "", "bad_value": None,
                          "severity": severity, "detail": detail})

    def frame(self):
        cols = ["pipeline", "check", "column", "source_row", "row_key", "bad_value", "severity", "detail"]
        return pd.DataFrame(self.rows, columns=cols)


def parse_typed(s, dtype, date_format=None):
    """Return (typed_series, bad_mask) for non-null raw strings. Nulls stay null and are not 'bad'."""
    null_mask = s.isin(NULL_TOKENS)
    s2 = s.where(~null_mask, None)
    if dtype in {"int", "integer"}:
        num = pd.to_numeric(s2, errors="coerce")
        bad = s2.notna() & (num.isna() | ((num % 1) != 0))
        typed = num.where(~bad).astype("Int64")
    elif dtype in {"float", "double", "decimal", "number", "numeric"}:
        num = pd.to_numeric(s2, errors="coerce")
        bad = s2.notna() & num.isna()
        typed = num.where(~bad)
    elif dtype in {"date", "datetime", "timestamp"}:
        dt = pd.to_datetime(s2, format=date_format, errors="coerce") if date_format \
            else pd.to_datetime(s2, errors="coerce")
        bad = s2.notna() & dt.isna()
        typed = dt
    elif dtype in {"bool", "boolean"}:
        low = s2.str.strip().str.lower()
        ok = low.isin(TRUE_TOKENS | FALSE_TOKENS)
        bad = s2.notna() & ~ok
        typed = low.map(lambda v: True if v in TRUE_TOKENS else (False if v in FALSE_TOKENS else None))
    else:
        bad = pd.Series(False, index=s.index)
        typed = s2
    return typed, bad, null_mask


def coerce_bound(v, dtype):
    if v is None:
        return None
    if dtype in {"date", "datetime", "timestamp"}:
        return pd.to_datetime(v)
    return float(v)


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------
def validate(df, rules, biz, pipeline, key_cols):
    log = ErrorLog(pipeline, df, key_cols)
    spec_issues = []
    n = len(df)

    # 1. Structural ------------------------------------------------------
    if n == 0:
        log.add_dataset("structure.empty", "", "error", "Dataset has zero rows")
        return log.frame(), pd.DataFrame(), pd.DataFrame(spec_issues), pd.DataFrame()

    expected = [r["column"] for r in rules]
    actual = list(df.columns)
    for c in expected:
        if c not in actual:
            # try a loose match to help spot renamed columns
            loose = [a for a in actual if _norm(a) == _norm(c)]
            hint = f" (close match in data: {loose[0]!r})" if loose else ""
            log.add_dataset("structure.missing_column", c, "error", f"Expected column not found{hint}")
    for c in actual:
        if c not in expected:
            log.add_dataset("structure.unexpected_column", c, "warn", "Column in data but not in spec")
    dup = {c for c in actual if actual.count(c) > 1}
    for c in dup:
        log.add_dataset("structure.duplicate_column", c, "error", "Duplicate column name")
    for k in key_cols:
        if k not in actual:
            log.add_dataset("key.missing_column", k, "error", "Key column not in data")

    # 2. Key / grain -----------------------------------------------------
    present_keys = [k for k in key_cols if k in df.columns]
    if present_keys:
        for k in present_keys:
            log.add(df[k].isin(NULL_TOKENS), "key.null", k, "error", "Null key component")
        dupmask = df.duplicated(subset=present_keys, keep=False)
        log.add(dupmask, "key.duplicate", "|".join(present_keys), "error",
                "Key not unique at declared grain",
                values=df[present_keys].agg("|".join, axis=1))

    # 3. Column-level rules ---------------------------------------------
    typed_df = pd.DataFrame(index=df.index)
    for r in rules:
        c, sev = r["column"], r["severity"]
        if c not in df.columns:
            continue
        raw = df[c]
        typed, bad_type, null_mask = parse_typed(raw, r["dtype"], r["date_format"])
        typed_df[c] = typed

        if not r["nullable"]:
            log.add(null_mask, "column.null_not_allowed", c, sev, "Required column is null/blank")
        # placeholder values masquerading as data (only if not an allowed value)
        ph = raw.str.strip().str.upper().isin(PLACEHOLDER_TOKENS)
        if r["allowed"]:
            ph &= ~raw.isin(r["allowed"])
        log.add(ph, "column.placeholder_value", c, "warn", "Placeholder text used instead of a null/real value")

        log.add(bad_type, "column.type_mismatch", c, sev, f"Value does not parse as {r['dtype']}")

        # whitespace hygiene
        ws = raw.ne(raw.str.strip()) & ~null_mask
        log.add(ws, "column.whitespace", c, "warn", "Leading/trailing whitespace")

        nn = ~null_mask
        if r["allowed"]:
            allowed = set(r["allowed"])
            if r["dtype"] in {"int", "integer", "float", "double", "number", "numeric", "decimal"}:
                # compare numerically so 1 vs 1.0 is not a false positive
                try:
                    allowed_num = {float(a) for a in allowed}
                    notin = nn & ~bad_type & ~typed.astype(float).isin(allowed_num)
                except ValueError:
                    notin = nn & ~raw.isin(allowed)
            else:
                notin = nn & ~raw.isin(allowed)
                case_only = notin & raw.str.strip().str.lower().isin({a.lower() for a in allowed})
                log.add(case_only, "column.domain_case_or_space", c, sev,
                        "Value matches an allowed value only after trimming/case-folding")
                notin &= ~case_only
            log.add(notin, "column.domain", c, sev, f"Not in allowed values ({len(allowed)} expected)")

        if r["min"] is not None or r["max"] is not None:
            if r["dtype"] in {"int", "integer", "float", "double", "number", "numeric", "decimal",
                              "date", "datetime", "timestamp"}:
                try:
                    lo = coerce_bound(r["min"], r["dtype"])
                    hi = coerce_bound(r["max"], r["dtype"])
                    ok_typed = nn & ~bad_type
                    if lo is not None:
                        log.add(ok_typed & (typed < lo), "column.below_min", c, sev, f"Below min {r['min']}")
                    if hi is not None:
                        log.add(ok_typed & (typed > hi), "column.above_max", c, sev, f"Above max {r['max']}")
                except Exception as e:
                    spec_issues.append({"column": c, "issue": f"Could not apply min/max: {e}"})
            else:
                spec_issues.append({"column": c, "issue": "min/max given for non-numeric/date dtype; use min_length/max_length"})

        if r["min_length"] is not None:
            log.add(nn & (raw.str.len() < r["min_length"]), "column.too_short", c, sev,
                    f"Shorter than {r['min_length']}")
        if r["max_length"] is not None:
            log.add(nn & (raw.str.len() > r["max_length"]), "column.too_long", c, sev,
                    f"Longer than {r['max_length']}")

        if r["regex"]:
            try:
                pat = re.compile(r["regex"])
                fails = nn & ~raw.map(lambda v: bool(pat.fullmatch(v)))
                log.add(fails, "column.regex", c, sev, f"Does not match {r['regex']}")
            except re.error as e:
                spec_issues.append({"column": c, "issue": f"Invalid regex in spec: {e}"})

        if r["unique"]:
            log.add(nn & raw.duplicated(keep=False), "column.not_unique", c, sev, "Duplicate value in unique column")

    # 4. Cross-column business rules ------------------------------------
    if biz is not None:
        for _, b in biz.iterrows():
            rid = b.get("rule_id") if not _blank(b.get("rule_id")) else "unnamed"
            sev = str(b.get("severity")).strip().lower() if not _blank(b.get("severity")) else "error"
            try:
                viol = typed_df.eval(str(b["violation_expr"]), engine="python").fillna(False).astype(bool)
                log.add(viol, f"business.{rid}", "", sev, str(b.get("description", "")),
                        values=pd.Series(str(b["violation_expr"]), index=df.index))
            except Exception as e:
                spec_issues.append({"column": f"business.{rid}", "issue": f"Expression failed: {e}"})

    errors = log.frame()

    # 5. Profile (dataset-level context, not pass/fail) -----------------
    prof = []
    for c in df.columns:
        raw = df[c]
        nulls = raw.isin(NULL_TOKENS)
        prof.append({"pipeline": pipeline, "column": c, "rows": n,
                     "null_pct": round(nulls.mean() * 100, 2),
                     "distinct": raw[~nulls].nunique(),
                     "top_values": "; ".join(f"{k} ({v})" for k, v in raw[~nulls].value_counts().head(5).items())})
    profile = pd.DataFrame(prof)

    # Summary ------------------------------------------------------------
    if len(errors):
        summ = (errors.groupby(["pipeline", "check", "column", "severity"], dropna=False)
                .agg(rows_affected=("source_row", "nunique"), total_flags=("check", "size"),
                     example_keys=("row_key", lambda s: "; ".join(s[s != ""].unique()[:3])),
                     example_values=("bad_value", lambda s: "; ".join(map(str, s.dropna().unique()[:3]))))
                .reset_index())
        summ["pct_rows_affected"] = (summ["rows_affected"] / n * 100).round(2)
        # a rule failing on ~100% of rows is usually a spec problem, not a data problem
        summ["likely_spec_issue"] = summ["pct_rows_affected"] >= 95
        summ = summ.sort_values(["severity", "pct_rows_affected"], ascending=[True, False])
    else:
        summ = pd.DataFrame(columns=["pipeline", "check", "column", "severity", "rows_affected"])

    return errors, summ, pd.DataFrame(spec_issues), profile


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pipeline", required=True, help="Label, e.g. OCHIN or PETER")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--sheet", default=None, help="Spec sheet name (default: first sheet)")
    ap.add_argument("--data", required=True)
    ap.add_argument("--key", default="", help="Comma-separated key columns (the grain)")
    ap.add_argument("--out", default="qc_out")
    a = ap.parse_args()

    key_cols = [k.strip() for k in a.key.split(",") if k.strip()]
    rules, biz = load_spec(a.spec, a.sheet)
    df = load_data(a.data)
    print(f"[{a.pipeline}] {len(df):,} rows x {len(df.columns)} cols; {len(rules)} spec columns")

    errors, summ, spec_issues, profile = validate(df, rules, biz, a.pipeline, key_cols)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    errors.to_csv(out / f"{a.pipeline}_errors.csv", index=False)
    summ.to_csv(out / f"{a.pipeline}_summary.csv", index=False)
    with pd.ExcelWriter(out / f"{a.pipeline}_qc.xlsx") as xw:
        summ.to_excel(xw, "summary", index=False)
        errors.head(200_000).to_excel(xw, "errors", index=False)
        profile.to_excel(xw, "profile", index=False)
        if len(spec_issues):
            spec_issues.to_excel(xw, "spec_issues", index=False)

    n_err = int((errors["severity"] == "error").sum()) if len(errors) else 0
    n_warn = int((errors["severity"] == "warn").sum()) if len(errors) else 0
    print(f"[{a.pipeline}] flags: {n_err:,} errors, {n_warn:,} warnings -> {out}/")
    if len(spec_issues):
        print(f"[{a.pipeline}] {len(spec_issues)} spec issue(s); see spec_issues tab")
    if len(summ):
        print(summ.head(15).to_string(index=False))
    sys.exit(1 if n_err else 0)


if __name__ == "__main__":
    main()
