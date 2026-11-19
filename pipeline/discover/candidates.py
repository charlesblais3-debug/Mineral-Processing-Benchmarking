"""Import candidate report links found by search agents (reports/_candidates/*.csv) into the catalog,
and optionally download + parse them.

CSV columns: url,issuer,project,commodity,ore_type,standard,study_level,report_date,effective_date,source_page,verified,notes
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from pipeline import catalog
from pipeline.parse import document

ROOT = Path(__file__).resolve().parents[2]
CAND = ROOT / "reports" / "_candidates"


def _rid(row: dict) -> str:
    return f"site-{document.slug(row['issuer'], 30)}-{document.slug(row['project'], 30)}"


def load(files: list[Path] | None = None) -> list[dict]:
    out = []
    for f in files or sorted(CAND.glob("*.csv")):
        with f.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("url", "").startswith("http"):
                    r["_file"] = f.name
                    out.append(r)
    return out


def merge(cands: list[dict]) -> dict:
    rows = catalog.read()
    new = 0
    for c in cands:
        title = f"title: {c['issuer']} – {c['project']} {c.get('standard','')} {c.get('study_level','')} {c.get('report_date','')}".strip()
        row = {"url": c["url"], "source": "edgar" if "sec.gov" in c["url"] else "agent_search", "issuer": c["issuer"],
               "commodity": c.get("commodity", ""), "standard": c.get("standard", ""), "study_level": c.get("study_level", ""),
               "matched_queries": f"agent:{c['_file']}", "project": c["project"],
               "note": f"{title} | {c.get('notes','')} | found on {c.get('source_page','')}"[:1000]}
        if catalog.merge(rows, row):
            new += 1
        r = rows[c["url"]]
        if r.get("status") in ("", "new", None) and c.get("verified", "").lower() == "yes":
            r["status"] = "is_report"
        r.setdefault("project", c["project"])
        if not r.get("project"):
            r["project"] = c["project"]
    catalog.write(rows)
    return {"candidates": len(cands), "new": new}


def parse(cands: list[dict], only_missing: bool = True) -> list[tuple[str, str]]:
    from pipeline.http import PoliteClient
    rows = catalog.read()
    client = PoliteClient(default_interval=3, min_interval={"www.sec.gov": 0.2})
    done = []
    for c in cands:
        r = rows[c["url"]]
        if r.get("status") != "is_report":
            continue
        if only_missing and r.get("report_id") and (ROOT / "reports" / r["report_id"] / "parsed").exists():
            continue
        if "sec.gov" not in c["url"]:
            r["report_id"] = r.get("report_id") or _rid(c)
        try:
            folder = document.process(r, client)
            r["report_id"] = folder.name
            done.append((c["project"], folder.name))
            print(f"  parsed {c['project']:<28} -> {folder.name}", flush=True)
        except Exception as e:  # noqa: BLE001 - one bad link must not stop the batch
            r["note"] = f"PARSE FAILED: {type(e).__name__}: {str(e)[:120]} | " + r.get("note", "")
            print(f"  FAILED {c['project']:<28} {type(e).__name__}: {str(e)[:100]}", flush=True)
        catalog.write(rows)
    return done


def merge_issuers() -> int:
    """Append issuer lines from reports/_candidates/*_issuers.yaml that are not in config/issuers.yaml yet."""
    cfg = ROOT / "config" / "issuers.yaml"
    text = cfg.read_text(encoding="utf-8")
    have = set(re.findall(r"name:\s*([^,]+),", text))
    added = []
    for f in sorted(CAND.glob("*_issuers.yaml")):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*-\s*\{\s*name:\s*([^,]+),", line)
            if m and m.group(1).strip() not in {h.strip() for h in have}:
                added.append("  " + line.strip())
                have.add(m.group(1))
    if added:
        cfg.write_text(text.rstrip() + "\n  # added from search-agent candidates\n" + "\n".join(added) + "\n", encoding="utf-8")
    return len(added)
