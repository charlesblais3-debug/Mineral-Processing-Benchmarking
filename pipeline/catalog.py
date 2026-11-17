"""reports/catalog.csv: every report link the pipeline knows about (tracked in git, opens in Excel).

One row per document URL. Discovery adds rows; later stages fill in the remaining columns.
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "reports" / "catalog.csv"

COLUMNS = [
    "url",             # direct link to the document (PDF or HTML)
    "source",          # edgar | company_site | search | inbox
    "issuer",
    "cik",             # SEC company id (EDGAR only)
    "form",            # 10-K, 8-K, 6-K, 40-F, S-1 ...
    "exhibit",         # EX-96.1, EX-99.1 ...
    "filed",           # filing date (ISO)
    "sic",
    "matched_queries", # which discovery queries found it (; separated)
    "status",          # new | is_report | not_report | duplicate | extracted | failed
    "report_id",       # folder name under reports/ once processed
    "project",
    "commodity",
    "standard",
    "study_level",
    "effective_date",
    "note",
]


def read(path: Path = CATALOG) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {row["url"]: row for row in csv.DictReader(f)}


def write(rows: dict[str, dict], path: Path = CATALOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows.values(), key=lambda r: (r.get("issuer", ""), r.get("filed", ""), r["url"]))
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in ordered:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    tmp.replace(path)


def merge(rows: dict[str, dict], new: dict) -> bool:
    """Add or update a row. Returns True if the URL is new. Never overwrites filled-in fields."""
    url = new["url"]
    if url not in rows:
        rows[url] = {c: "" for c in COLUMNS} | {k: v for k, v in new.items() if k in COLUMNS}
        rows[url]["status"] = rows[url]["status"] or "new"
        return True
    row = rows[url]
    for k, v in new.items():
        if k == "matched_queries":
            have = set(filter(None, row.get(k, "").split(";")))
            row[k] = ";".join(sorted(have | set(filter(None, str(v).split(";")))))
        elif k in COLUMNS and not row.get(k):
            row[k] = v
    return False
