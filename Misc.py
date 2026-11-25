Path("docs").mkdir(exist_ok=True)
if not Path("docs/index.md").exists():
    Path("docs/index.md").write_text("# ELT Documentation\n\nArchitecture and data flow.\n")

for key, spec in SPECS.items():
    d = Path("docs") / key
    d.mkdir(parents=True, exist_ok=True)
    (d / "fields.md").write_text(TPL.render(**spec), encoding="utf-8")  # always regenerated
    if not (d / "overview.md").exists():                                # never overwritten
        (d / "overview.md").write_text(
            f"# {spec['title']}\n\nPurpose, owner, known issues, change log.\n",
            encoding="utf-8")
