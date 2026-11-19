"""Helpers for writing extraction files quickly (Option A sessions).

    from pipeline.extract.compose import F, C, save
    save(report_id, report={...}, flowsheet={...}, facts=[F(...)], costs=[C(...)])

Items may omit `page`: it is filled with the first page whose text contains the quote.
Items whose quote is found nowhere are reported and left out, so nothing unverifiable is saved.
"""
from __future__ import annotations

import json

from pipeline.extract.load import REPORTS, find_quote


def F(parameter, value, unit, quote, page=None, **k):
    return {"parameter": parameter, "value": value, "unit": unit, "quote": quote, "page": page, **k}


def C(cost_type, raw_label, value, quote, page=None, **k):
    return {"cost_type": cost_type, "raw_label": raw_label, "value": value, "quote": quote, "page": page, **k}


def _place(rid, item, near=None):
    if item.get("page"):
        return True
    pages = find_quote(rid, item["quote"])
    if not pages:
        return False
    if near:  # prefer the occurrence closest to a hint page (e.g. the costs chapter)
        pages.sort(key=lambda p: abs(p - near))
    item["page"] = pages[0]
    return True


def _digits_in_quote(item) -> bool:
    """True if the value (as written, with or without thousands separators) appears in the quote."""
    v = item.get("value")
    if not isinstance(v, (int, float)):
        return True
    q = item["quote"].replace(",", "").replace(" ", "")
    forms = {f"{v:g}", f"{abs(v):g}", f"{v:.1f}", f"{v:.2f}", f"{v:.0f}", f"{v:.3f}"}
    return any(f.replace(",", "") in q for f in forms)


def save(rid, report, flowsheet=None, facts=(), costs=(), near=None):
    warn = [f"value {i.get('value')} not in quote for {i.get('parameter') or i.get('raw_label')}"
            for i in list(facts) + list(costs) if not _digits_in_quote(i)]
    if warn:
        print("CHECK:", *warn, sep="\n  ")
    missing = []
    rep = dict(report)
    if "effective_date_quote" in rep and not rep.get("effective_date_page"):
        pg = find_quote(rid, rep["effective_date_quote"])
        if pg:
            rep["effective_date_page"] = pg[0]
        else:
            missing.append("effective_date")
    keep_f = [f for f in facts if _place(rid, f, near) or missing.append(f["parameter"])]
    keep_c = [c for c in costs if _place(rid, c, near) or missing.append(c["raw_label"])]
    if flowsheet and not _place(rid, flowsheet):
        missing.append("flowsheet")
        flowsheet = None
    data = {"extractor": "claude-code", "report": rep, "flowsheet": flowsheet, "facts": keep_f, "costs": keep_c}
    out = REPORTS / rid / "extracted"
    out.mkdir(exist_ok=True)
    (out / "v1.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return missing
