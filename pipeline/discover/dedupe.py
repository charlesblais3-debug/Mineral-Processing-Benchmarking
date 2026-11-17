"""Mark re-filed copies of the same report as duplicates, keeping the latest filing.

Producers re-file the same technical report with every annual filing, and the same report is
often attached to several forms. Rows are grouped by issuer + a normalized title; within a
group the most recent filing stays `is_report`, the others become `duplicate`.
"""
from __future__ import annotations

import re

from pipeline import catalog

NOISE = [
    r"\S+\.html?", r"ex-?\d+(\.\d+)?", r"exhibit\s*\d+(\.\d+)*", r"filed by \S+", r"document",
    r"table of contents", r"newsfilecorp\.com", r"[a-z0-9_]+ex\d+\S*",
]


def title_key(row: dict) -> str:
    note = row.get("note", "")
    title = note.split("title:", 1)[1] if "title:" in note else ""
    t = title.lower()
    for pat in NOISE:
        t = re.sub(pat, " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = [w for w in t.split() if len(w) > 1][:12]
    issuer = re.sub(r"\s*\(.*$", "", row.get("issuer", "")).lower().strip()
    return issuer + "|" + " ".join(words)


def run() -> dict:
    rows = catalog.read()
    groups: dict[str, list[dict]] = {}
    for r in rows.values():
        if r["status"] == "is_report":
            groups.setdefault(title_key(r), []).append(r)
    dups = 0
    for members in groups.values():
        members.sort(key=lambda r: (r.get("filed", ""), r["url"]), reverse=True)
        keep = members[0]
        for r in members[1:]:
            r["status"] = "duplicate"
            r["note"] = f"duplicate of {keep['url']} | " + r.get("note", "")
            dups += 1
    catalog.write(rows)
    return {"groups": len(groups), "duplicates_marked": dups}
