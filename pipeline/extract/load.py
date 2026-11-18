"""Validate an extraction file and load it into the database.

An extraction file is reports/<report_id>/extracted/v1.json, written by a Claude Code session
(Option A) or by an API extractor, following EXTRACTION.md. Every value must carry a page
number and a verbatim quote; the loader checks the quote against the cached page text.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from pipeline import taxonomy
from pipeline.db.init_db import init_db

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports"
EXTRACTOR_VERSION = "v1"

UNIT_FACTORS = {  # (from, to): factor
    ("kWh/st", "kWh/t"): 1.10231, ("kWh/sh t", "kWh/t"): 1.10231, ("kWh/ton", "kWh/t"): 1.10231,
    ("kWh/t", "kWh/t"): 1.0,
    ("Mt/a", "Mt/a"): 1.0, ("Mtpa", "Mt/a"): 1.0,
    ("t/d", "t/d"): 1.0, ("tpd", "t/d"): 1.0, ("st/d", "t/d"): 0.907185,
    ("µm", "µm"): 1.0, ("um", "µm"): 1.0, ("mm", "µm"): 1000.0, ("mesh", "µm"): None,
}
MULTIPLIERS = {"": 1, "k": 1e3, "thousand": 1e3, "M": 1e6, "million": 1e6, "B": 1e9, "billion": 1e9}


def _squash(text: str) -> str:
    text = text.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"[|$€£,]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_on_page(report_dir: Path, page: int, quote: str, slack: int = 1) -> bool:
    """True if the quote appears on the page (or a neighbouring page, for text split by a page break)."""
    q = _squash(quote)
    if not q:
        return False
    pages_dir = report_dir / "parsed" / "pages"
    for p in range(max(1, page - slack), page + slack + 1):
        f = pages_dir / f"{p:04d}.md"
        if f.exists() and q in _squash(f.read_text(encoding="utf-8")):
            return True
    return False


def normalize(param: dict, value: float, unit: str) -> tuple[float | None, str]:
    target = param["unit"]
    if unit == target:
        return value, target
    factor = UNIT_FACTORS.get((unit, target))
    if factor is None:
        return None, target
    return value * factor, target


def load_report(report_id: str, con: sqlite3.Connection | None = None) -> dict:
    folder = REPORTS / report_id
    data = json.loads((folder / "extracted" / f"{EXTRACTOR_VERSION}.json").read_text(encoding="utf-8"))
    src = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    params = taxonomy.load("parameters")["parameters"]
    con = con or init_db()
    problems: list[str] = []
    info = data["report"]
    extractor = data.get("extractor", "claude-code")

    # ---- report-level date must be quoted too
    if info.get("effective_date_quote"):
        if not quote_on_page(folder, info["effective_date_page"], info["effective_date_quote"]):
            problems.append("report: effective_date quote not found")
    else:
        problems.append("report: effective_date has no page/quote")

    # ---- project + report rows
    project_id = taxonomy_slug(info["project"])
    con.execute(
        "INSERT INTO projects(id, name, country, region, primary_commodity, by_products, mine_type, status)"
        " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, country=excluded.country,"
        " region=excluded.region, primary_commodity=excluded.primary_commodity, by_products=excluded.by_products,"
        " mine_type=excluded.mine_type, status=excluded.status",
        (project_id, info["project"], info.get("country"), info.get("region"), info.get("primary_commodity"),
         json.dumps(info.get("by_products", [])), info.get("mine_type"), info.get("project_status")),
    )
    con.execute("DELETE FROM reports WHERE id=?", (report_id,))  # cascades facts/costs/flowsheet
    con.execute(
        "INSERT INTO reports(id, project_id, title, issuer, standard, study_level, effective_date, publish_date,"
        " qps, source_url, sha256, pages, currency, cost_base_year, parse_status)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'extracted')",
        (report_id, project_id, info.get("title"), src.get("issuer"), info.get("standard"), info.get("study_level"),
         info.get("effective_date"), src.get("filed"), json.dumps(info.get("qps", [])), src["url"], src["sha256"],
         src.get("pages"), info.get("currency"), info.get("cost_base_year")),
    )

    # ---- flowsheet
    fs = data.get("flowsheet")
    if fs:
        commodities = [info.get("primary_commodity")] + info.get("by_products", [])
        try:
            derived = taxonomy.derive_flowsheet(fs["unit_ops"], [c for c in commodities if c])
            con.execute(
                "INSERT INTO flowsheets(report_id, unit_ops, families, comminution_circuit, description_quote, page)"
                " VALUES (?,?,?,?,?,?)",
                (report_id, json.dumps(fs["unit_ops"]), json.dumps(derived["families"]),
                 derived["comminution_circuit"], fs.get("quote"), fs.get("page")),
            )
        except ValueError as e:
            problems.append(f"flowsheet: {e}")

    # ---- facts
    n_facts = n_verified = 0
    for f in data.get("facts", []):
        key = f["parameter"]
        if key not in params:
            problems.append(f"fact: unknown parameter '{key}' (add it to parameters.yaml)")
            continue
        p = params[key]
        verified = quote_on_page(folder, f["page"], f["quote"])
        value = f.get("value")
        vnorm, unorm = (normalize(p, value, f.get("unit", p["unit"])) if isinstance(value, (int, float))
                        else (None, p["unit"]))
        status = "auto_accepted"
        if not verified:
            status = "needs_review"
            problems.append(f"fact {key}: quote not found on page {f['page']}")
        rng = p.get("range")
        if rng and vnorm is not None and not (rng[0] <= vnorm <= rng[1]):
            status = "needs_review"
            problems.append(f"fact {key}: {vnorm} {unorm} outside {rng}")
        con.execute(
            "INSERT INTO facts(report_id, parameter_key, metal, value, value_text, unit, value_norm, unit_norm,"
            " statistic, basis, context, page, quote, quote_verified, confidence, extractor, extractor_version,"
            " review_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_id, key, f.get("metal"), value if isinstance(value, (int, float)) else None,
             value if isinstance(value, str) else None, f.get("unit"), vnorm, unorm, f.get("statistic"),
             f.get("basis", "estimate"), json.dumps(f.get("context", {})), f["page"], f["quote"], int(verified),
             f.get("confidence"), extractor, EXTRACTOR_VERSION, status),
        )
        n_facts += 1
        n_verified += verified

    # ---- costs
    n_costs = 0
    totals: dict[str, float] = {}
    sums: dict[str, float] = {}
    parents: dict[str, float] = {}        # breakdown checks: parent label -> value
    parts: dict[str, float] = {}
    mapped: dict[str, tuple] = {}         # label -> (l1, l2), so breakdown lines can inherit their parent's category
    for c in data.get("costs", []):
        mult = MULTIPLIERS.get(c.get("multiplier", "M"), 1)
        value = c["value"] * mult
        note = None
        if c.get("per_short_ton"):           # $/short ton -> $/tonne (1 t = 1.10231 st)
            value = value * 1.10231
            note = f"converted from {c['value']} per short ton"
        if c.get("is_total") or c.get("is_subtotal"):
            l1, l2, method, conf = "total", None, "rule", 1.0      # totals are kept for checks, never categorized
        elif c.get("l1"):
            l1, l2, method, conf = c["l1"], c.get("l2"), "llm", c.get("confidence", 0.8)
        else:
            m = taxonomy.map_cost_label(c["raw_label"], c["cost_type"])
            l1, l2, method, conf = m.l1, m.l2, m.method, m.confidence
            parent = mapped.get(f"{c['cost_type']}/{c.get('basis_unit', 'total')}/{c.get('part_of')}")
            if c.get("part_of") and parent and (l1 == "other" or l1 != parent[0]):
                l1, l2, method, conf = parent[0], (l2 if l1 == parent[0] else "other"), "parent", 0.7
        mapped[f"{c['cost_type']}/{c.get('basis_unit', 'total')}/{c['raw_label']}"] = (l1, l2)
        verified = quote_on_page(folder, c["page"], c["quote"])
        status = "auto_accepted" if verified and conf >= 0.6 else "needs_review"
        if not verified:
            problems.append(f"cost '{c['raw_label']}': quote not found on page {c['page']}")
        group = f"{c['cost_type']}/{c.get('basis_unit', 'total')}"
        parents[f"{group}/{c['raw_label']}"] = value
        if c.get("outside_total"):                # listed by the report but outside its total
            pass
        elif c.get("part_of"):                      # breakdown of another line: checked separately
            key = f"{group}/{c['part_of']}"
            parts[key] = parts.get(key, 0.0) + value
        elif c.get("is_total"):
            totals[group] = value
        elif not c.get("is_subtotal"):
            sums[group] = sums.get(group, 0.0) + value
        con.execute(
            "INSERT INTO cost_items(report_id, cost_type, category_l1, category_l2, raw_label, value, currency,"
            " basis_unit, base_year, is_subtotal, includes_indirects, basis, page, quote, quote_verified,"
            " mapping_method, mapping_confidence, extractor, extractor_version, review_status, review_note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_id, c["cost_type"], l1, l2, c["raw_label"], value, c.get("currency", info.get("currency")),
             c.get("basis_unit", "total"), c.get("base_year", info.get("cost_base_year")),
             int(bool(c.get("is_subtotal") or c.get("is_total") or c.get("part_of"))), c.get("includes_indirects"),
             c.get("basis", "estimate"), c["page"], c["quote"], int(verified), method, conf, extractor,
             EXTRACTOR_VERSION, status, note),
        )
        n_costs += 1

    for group, total in totals.items():
        s = sums.get(group, 0.0)
        if total and abs(s - total) / total > 0.02:
            problems.append(f"costs {group}: line items sum to {s:,.0f} but reported total is {total:,.0f}")

    for key, s in parts.items():
        parent = parents.get(key)
        if parent is None:
            problems.append(f"costs {key}: breakdown has no parent line with that raw_label")
        elif parent and abs(s - parent) / abs(parent) > 0.02:
            problems.append(f"costs {key}: breakdown sums to {s:,.2f} but the line says {parent:,.2f}")

    con.commit()
    from pipeline.normalize import costs as normalize_costs
    normalize_costs.run(con)
    return {"report": report_id, "facts": n_facts, "facts_quote_verified": n_verified,
            "costs": n_costs, "problems": problems}


def taxonomy_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def find_quote(report_id: str, quote: str) -> list[int]:
    """Pages whose text contains the quote (same normalization as the loader's check)."""
    q = _squash(quote)
    pages_dir = REPORTS / report_id / "parsed" / "pages"
    return [int(f.stem) for f in sorted(pages_dir.glob("*.md")) if q in _squash(f.read_text(encoding="utf-8"))]
