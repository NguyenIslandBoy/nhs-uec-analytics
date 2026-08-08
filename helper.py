from pathlib import Path

p = Path("docs/known-issues.md")
t = p.read_text(encoding="utf-8")
old = t[t.index("### ODS-12") : t.index("### ODS-13")]
new = """### ODS-12 - Paired succession dates one day apart
Three predecessor-successor pairs carry two succession records dated on consecutive days
across a boundary: R1E->RRE (2018-05-31 / 2018-06-01), RJF->RTG (2018-06-30 / 2018-07-01),
RY1->RW4 (2018-03-31 / 2018-04-01). These are one transaction recorded from both ends - the
predecessor's final operational day and the successor's first. **Handling:** second
deduplication pass on ``(predecessor_code, successor_code)`` retaining the **later** date,
which is the successor's operational start and therefore the correct attribution boundary for
daily activity. Retaining the earlier date would attribute the predecessor's final day of
activity to the successor. Conflicts are counted and logged, not silently resolved.

"""
p.write_text(t.replace(old, new), encoding="utf-8")
print("patched")
