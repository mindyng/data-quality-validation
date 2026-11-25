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

SPECS = {
    "etl_a": {"title": "ETL A", "columns": {
        "degree": ["Bachelor's", "Master's", "Doctorate"],
        "status": ["Submitted", "Approved", "Denied"],
    }},
    "etl_b": {"title": "ETL B", "columns": {
        "pathway": ["Pathway 1", "Pathway 2"],
    }},
}

TPL = Template("""# {{ title }}: expected values

*Generated from the spec. Do not edit here; add notes in overview.md.*

| Column | Allowed values |
|--------|----------------|
{% for col, vals in columns.items() -%}
| {{ col }} | {{ vals | join(", ") }} |
{% endfor %}""")
