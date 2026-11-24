'''
project/
  build_docs.py      # generates fields.md, creates overview.md once
  mkdocs.yml
  docs/
    index.md         # hand-written: flow diagram, overview of both ETLs
    etl_a/overview.md   # hand-written (created once, never overwritten)
    etl_a/fields.md     # generated (overwritten every run)
    etl_b/...
'''

from pathlib import Path
from jinja2 import Template

# Replace with: from my_specs import SPECS  (same shape as below)
SPECS = {
    "etl_a": {"title": "ETL A", "fields": [
        {"id": 1001, "name": "account_id", "type": "varchar(10)",
         "nullable": False, "description": "Unique account identifier"},
    ]},
    "etl_b": {"title": "ETL B", "fields": [
        {"id": 2001, "name": "record_id", "type": "int",
         "nullable": False, "description": "Unique record identifier"},
    ]},
}

TPL = Template("""# {{ title }}: field dictionary

*Generated from the spec. Do not edit here; add notes in overview.md.*

| ID | Column | Type | Nullable | Description |
|----|--------|------|----------|-------------|
{% for f in fields -%}
| {{ f.id }} | {{ f.name }} | {{ f.type }} | {{ "Yes" if f.nullable else "No" }} | {{ f.description }} |
{% endfor %}""")

Path("docs").mkdir(exist_ok=True)
if not Path("docs/index.md").exists():
    Path("docs/index.md").write_text("# ELT Documentation\n\nArchitecture and data flow.\n")

for key, spec in SPECS.items():
    d = Path("docs") / key
    d.mkdir(parents=True, exist_ok=True)
    (d / "fields.md").write_text(TPL.render(**spec))        # always regenerated
    if not (d / "overview.md").exists():                    # never overwritten
        (d / "overview.md").write_text(
            f"# {spec['title']}\n\nPurpose, owner, known issues, change log.\n")
