"""Build the web interface from the database.

    python -m pipeline export-web

Reads data/benchmark.db and writes:
    app/web/benchmark.html   page body only (published as a claude.ai page)
    app/web/dist/index.html  full HTML document (open locally or host as a static site)
Every number in the export carries its source (report link, page, verbatim quote).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pipeline import taxonomy
from pipeline.db.init_db import DEFAULT_DB

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "app" / "web"

DESIGN_STATS = ("design", "p75", "p80", "p90")


def _src(row: dict, url: str) -> dict:
    return {"page": row["page"], "quote": row["quote"], "url": url, "verified": bool(row["quote_verified"])}


def build(db_path: Path = DEFAULT_DB) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cost_tax = taxonomy.load("cost_taxonomy")
    flow_tax = taxonomy.load("flowsheet_taxonomy")
    params = taxonomy.load("parameters")["parameters"]
    ppi_year = con.execute("SELECT max(escalation_year) FROM cost_items").fetchone()[0]

    projects = []
    for r in con.execute("SELECT r.*, p.name, p.country, p.region, p.primary_commodity, p.by_products, p.mine_type, "
                         "p.status FROM reports r JOIN projects p ON p.id = r.project_id ORDER BY p.name"):
        r = dict(r)
        url = r["source_url"]
        facts = [dict(f) for f in con.execute("SELECT * FROM facts WHERE report_id=? ORDER BY id", (r["id"],))]
        costs = [dict(c) for c in con.execute("SELECT * FROM cost_items WHERE report_id=? ORDER BY id", (r["id"],))]
        fs = con.execute("SELECT * FROM flowsheets WHERE report_id=?", (r["id"],)).fetchone()

        def best_fact(key: str, metal: str | None = None):
            cands = [f for f in facts if f["parameter_key"] == key and (metal is None or f["metal"] == metal)
                     and f["value_norm"] is not None]
            if not cands:
                return None
            cands.sort(key=lambda f: (f["statistic"] not in DESIGN_STATS, f["basis"] != "estimate"))
            f = cands[0]
            return {"value": f["value_norm"], "unit": f["unit_norm"], "statistic": f["statistic"], "basis": f["basis"],
                    "context": json.loads(f["context"] or "{}"), **_src(f, url)}

        def grand_total(ctype: str, basis: str = "total"):
            rows = [c for c in costs if c["cost_type"] == ctype and c["category_l2"] == "grand_total"
                    and c["basis_unit"] == basis and c["value_usd_escalated"] is not None]
            if not rows:
                return None
            c = max(rows, key=lambda r: r["value_usd_escalated"])   # loader allows one; be safe if several
            scale = 1e6 if basis == "total" else 1
            return {"value": c["value_usd_escalated"] / scale, "label": c["raw_label"], **_src(c, url)}

        def category_sum(ctype: str, l1: str, basis: str):
            rows = [c for c in costs if c["cost_type"] == ctype and c["category_l1"] == l1 and c["basis_unit"] == basis
                    and not c["is_subtotal"] and c["value_usd_escalated"] is not None]
            if not rows:
                return None
            scale = 1e6 if basis == "total" else 1
            return {"value": sum(c["value_usd_escalated"] for c in rows) / scale,
                    "label": " + ".join(c["raw_label"] for c in rows), **_src(rows[0], url)}

        tpd = best_fact("throughput_tpd")
        if tpd is None:
            mt = best_fact("throughput_mtpa")
            if mt:
                tpd = {**mt, "value": mt["value"] * 1e6 / 365, "unit": "t/d", "derived": "Mt/a ÷ 365"}
        metal = r["primary_commodity"]
        metrics = {
            "tpd": tpd,
            "capex": grand_total("initial_capex"),
            "plant_capex": category_sum("initial_capex", "process", "total"),
            "site_opex": grand_total("opex", "per_t_milled"),
            "product_opex": grand_total("opex", "per_t_concentrate"),
            "process_opex": category_sum("opex", "processing", "per_t_milled"),
            "bwi": best_fact("bwi_ball"),
            "axb": best_fact("axb"),
            "p80": best_fact("grind_p80_um"),
            "recovery": best_fact("recovery_lom", metal),
            "head_grade": best_fact("head_grade_lom", metal),
            "aisc": best_fact("aisc", metal),
        }
        projects.append({
            "id": r["project_id"], "report_id": r["id"], "name": r["name"], "commodity": metal,
            "by_products": json.loads(r["by_products"] or "[]"), "country": r["country"], "region": r["region"],
            "mine_type": r["mine_type"], "status": r["status"], "study_level": r["study_level"],
            "standard": r["standard"], "issuer": r["issuer"], "effective_date": r["effective_date"],
            "title": r["title"], "url": url, "currency": r["currency"], "cost_base_year": r["cost_base_year"],
            "families": json.loads(fs["families"]) if fs else [], "circuit": fs["comminution_circuit"] if fs else None,
            "unit_ops": json.loads(fs["unit_ops"]) if fs else [],
            "flowsheet_src": {"page": fs["page"], "quote": fs["description_quote"], "url": url} if fs else None,
            "metrics": {k: v for k, v in metrics.items() if v},
            "facts": [{"key": f["parameter_key"], "label": params[f["parameter_key"]]["label"],
                       "group": params[f["parameter_key"]]["group"], "metal": f["metal"],
                       "value": f["value_norm"] if f["value_norm"] is not None else f["value"],
                       "unit": f["unit_norm"] if f["value_norm"] is not None else f["unit"],
                       "raw": f"{f['value']:g} {f['unit']}" if f["value"] is not None else f["value_text"],
                       "statistic": f["statistic"], "basis": f["basis"], "context": json.loads(f["context"] or "{}"),
                       **_src(f, url)} for f in facts],
            "costs": [{"type": c["cost_type"], "l1": c["category_l1"], "l2": c["category_l2"], "label": c["raw_label"],
                       "value": c["value"], "currency": c["currency"], "usd": c["value_usd"],
                       "usd_esc": c["value_usd_escalated"], "basis": c["basis_unit"], "subtotal": bool(c["is_subtotal"]),
                       "note": c["review_note"], **_src(c, url)} for c in costs],
        })

    labels = {
        "cost_l1": {t: {k: v["label"] for k, v in cost_tax[t].items()} for t in ("capex", "opex")},
        "families": {f["key"]: f["label"] for f in flow_tax["families"]},
        "circuits": {c["key"]: c["label"] for c in flow_tax["comminution_circuits"]},
        "unit_ops": {k: v["label"] for k, v in flow_tax["unit_ops"].items()},
    }
    return {"escalation_year": ppi_year, "projects": projects, "labels": labels}


def write(data: dict) -> list[Path]:
    template = (WEB / "template.html").read_text(encoding="utf-8")
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    body = template.replace("/*__DATA__*/null", blob)
    (WEB / "dist").mkdir(parents=True, exist_ok=True)
    page = WEB / "benchmark.html"
    page.write_text(body, encoding="utf-8")
    full = WEB / "dist" / "index.html"
    full.write_text("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
                    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">"
                    "</head><body>\n" + body + "\n</body></html>\n", encoding="utf-8")
    return [page, full]
