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
    ("Mt/a", "Mt/a"): 1.0, ("Mtpa", "Mt/a"): 1.0, ("Mt/y", "Mt/a"): 1.0, ("Mty", "Mt/a"): 1.0,
    ("t/d", "t/d"): 1.0, ("t/month", "t/d"): 12 / 365, ("tpm", "t/d"): 12 / 365, ("kt/month", "t/d"): 12000 / 365,
    ("Mt/month", "Mt/a"): 12.0, ("kt/month", "Mt/a"): 0.012, ("tpd", "t/d"): 1.0, ("st/d", "t/d"): 0.907185, ("LT/d", "t/d"): 1.01605,
    ("MLT/y", "Mt/a"): 1.01605, ("Mst/y", "Mt/a"): 0.907185,
    ("µm", "µm"): 1.0, ("um", "µm"): 1.0, ("mm", "µm"): 1000.0, ("mesh", "µm"): None,
}
MULTIPLIERS = {"": 1, "k": 1e3, "000": 1e3, "thousand": 1e3, "thousands": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6,
               "millions": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9, "billions": 1e9}


def multiplier(m) -> float | None:
    """Case-insensitive ("K" and "k" both mean thousand). None for anything unrecognised, so a typo is a
    load problem instead of silently scaling a cost by 1."""
    return MULTIPLIERS.get(str(m).strip().lower().lstrip("$"))


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


def value_in_quote(value, quote: str) -> bool:
    """True if the number appears in the quote as written (allowing thousands separators, spaces and a
    x1000 / x1e6 scale change such as US$2.04 billion -> 2,040 MUSD). Derived values must say so."""
    if not isinstance(value, (int, float)):
        return True
    q = re.sub(r"(?<=\d) ?, ?(?=\d{3}(?!\d))", "", quote)   # 1,234,567 / "2 , 040" -> 1234567 / 2040
    q = q.replace(" . ", ".").replace(" .", ".")             # "37 . 3" style spacing in some PDFs
    nums = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", q)}
    # Space as thousands separator ("5 455", common in European reports). Kept as extra candidates so
    # table rows of separate numbers still match individually.
    qs = re.sub(r"(?<=\d)[ \u00a0\u202f](?=\d{3}(?!\d))", "", q)
    nums |= {float(n) for n in re.findall(r"\d+(?:\.\d+)?", qs)}
    v = abs(value)
    for scale in (1, 1e3, 1e-3, 1e6, 1e-6, 1e-2):   # 1e-2: 97.5 % stated as 0.975
        target = v * scale
        if any(abs(n - target) <= max(1e-9, abs(target) * 1e-6) for n in nums):
            return True
    return False


CURRENCY_MARKERS = {"USD": r"US\$|USD|US dollars", "CAD": r"C\$|CAD|CA\$|Canadian", "AUD": r"A\$|AU\$|AUD|Australian dollar"}


def currency_conflict(currency: str | None, quote: str) -> str | None:
    """Name of another currency the quote explicitly uses, when it differs from the item's currency."""
    if not currency:
        return None
    for cur, pat in CURRENCY_MARKERS.items():
        if cur != currency and re.search(pat, quote) and not re.search(CURRENCY_MARKERS.get(currency, "^$"), quote):
            return cur
    return None


NATIVE_FACTORS = {          # (stated unit, native unit) -> factor, for per-metal ("native") parameters
    ("g/t", "g/t"): 1.0, ("gpt", "g/t"): 1.0, ("oz/t", "g/t"): 34.2857, ("oz/st", "g/t"): 34.2857, ("oz/ton", "g/t"): 34.2857,
    ("%", "%"): 1.0, ("ppm", "%"): 1e-4,
    ("USD/oz", "USD/oz"): 1.0, ("US$/oz", "USD/oz"): 1.0,
    ("USD/lb", "USD/lb"): 1.0, ("US$/lb", "USD/lb"): 1.0, ("USD/t", "USD/lb"): 1 / 2204.62, ("USD/tonne", "USD/lb"): 1 / 2204.62,
    ("oz", "oz"): 1.0, ("koz", "oz"): 1e3, ("Moz", "oz"): 1e6, ("lb", "lb"): 1.0, ("Mlb", "lb"): 1e6, ("t", "lb"): 2204.62,
    ("kt", "lb"): 2.20462e6,
}
NATIVE_KIND = {"native": "grade", "native_price": "price", "native_per_year": "production"}


def native_unit(param: dict, metal: str | None) -> str | None:
    kind = NATIVE_KIND.get(param["unit"])
    if not kind or not metal:
        return None
    return (taxonomy.load("parameters").get("native_units", {}).get(metal) or {}).get(kind)


def normalize(param: dict, value: float, unit: str, metal: str | None = None) -> tuple[float | None, str]:
    target = param["unit"]
    if target in NATIVE_KIND:                    # per-metal unit (g/t Au, % Cu, USD/oz, USD/lb ...)
        nat = native_unit(param, metal)
        if not nat:
            return None, target
        if param["unit"] == "native_per_year":
            unit = unit.replace("/a", "").replace("/y", "").replace("/yr", "").strip()
        factor = NATIVE_FACTORS.get((unit, nat))
        return (value * factor, nat) if factor is not None else (None, nat)
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
        if not (fs.get("quote") and fs.get("page") and quote_on_page(folder, fs["page"], fs["quote"])):
            problems.append("flowsheet: quote not found on its page")
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
        vnorm, unorm = (normalize(p, value, f.get("unit", p["unit"]), f.get("metal")) if isinstance(value, (int, float))
                        else (None, p["unit"]))
        status = "auto_accepted"
        if not verified:
            status = "needs_review"
            problems.append(f"fact {key}: quote not found on page {f['page']}")
        if not f.get("derived") and not value_in_quote(value, f["quote"]):
            status = "needs_review"
            problems.append(f"fact {key}: value {value} not in its quote (mark derived: true with a note if computed)")
        cur = f.get("currency")
        for code in ("USD", "CAD", "AUD", "EUR", "GBP", "BRL", "ZAR"):
            if cur and cur != code and code in str(f.get("unit", "")):
                status = "needs_review"
                problems.append(f"fact {key}: unit {f.get('unit')} contradicts currency {cur}")
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
    tolerance: dict[str, float] = {}
    grand_totals: dict[str, str] = {}
    sums: dict[str, float] = {}
    parents: dict[str, float] = {}        # breakdown checks: parent label -> value
    parts: dict[str, float] = {}
    partial_parents: set[str] = set()
    mapped: dict[str, tuple] = {}         # label -> (l1, l2), so breakdown lines can inherit their parent's category
    for c in data.get("costs", []):
        mult = multiplier(c.get("multiplier", "M"))
        if mult is None:
            problems.append(f"cost '{c['raw_label']}': unknown multiplier {c.get('multiplier')!r}")
            mult = 1
        value = c["value"] * mult
        note = None
        if c.get("per_short_ton"):           # $/short ton -> $/tonne (1 t = 1.10231 st)
            value = value * 1.10231
            note = f"converted from {c['value']} per short ton"
        elif c.get("per_long_ton"):          # $/long ton (2,240 lb) -> $/tonne (1 LT = 1.01605 t)
            value = value / 1.01605
            note = f"converted from {c['value']} per long ton"
        if c.get("is_total") or c.get("is_subtotal"):
            l1, l2, method, conf = "total", ("grand_total" if c.get("is_total") else "subtotal"), "rule", 1.0
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
        if not c.get("derived") and not value_in_quote(c["value"], c["quote"]):
            status = "needs_review"
            problems.append(f"cost '{c['raw_label']}': value {c['value']} not in its quote")
        if c.get("review_note"):                   # extractor flags something a human should look at
            status = "needs_review"
            note = f"{note}; {c['review_note']}" if note else c["review_note"]
        other = currency_conflict(c.get("currency", info.get("currency")), c["quote"])
        if other:
            status = "needs_review"
            problems.append(f"cost '{c['raw_label']}': quote is in {other} but the item is {c.get('currency', info.get('currency'))}")
        group = f"{c['cost_type']}/{c.get('basis_unit', 'total')}"
        parents[f"{group}/{c['raw_label']}"] = value
        if c.get("partial_breakdown"):
            partial_parents.add(f"{group}/{c['raw_label']}")
        if c.get("outside_total"):                # listed by the report but outside its total
            pass
        elif c.get("part_of"):                      # breakdown of another line: checked separately
            key = f"{group}/{c['part_of']}"
            parts[key] = parts.get(key, 0.0) + value
        elif c.get("is_total"):
            if group in grand_totals:
                problems.append(f"costs {group}: more than one is_total ('{grand_totals[group]}' and '{c['raw_label']}') - mark intermediate totals is_subtotal")
            grand_totals[group] = c["raw_label"]
            if not c.get("partial_breakdown"):      # report gives only some of the lines: nothing to check
                totals[group] = value
                tolerance[group] = c.get("tolerance_pct", 2) / 100   # rounded tables (e.g. US$ 0.1 B steps)
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
        if group not in sums:          # a total reported on its own: nothing to check against
            continue
        if total and abs(s - total) / total > tolerance[group]:
            problems.append(f"costs {group}: line items sum to {s:,.0f} but reported total is {total:,.0f}")

    for key, s in parts.items():
        parent = parents.get(key)
        if key in partial_parents:           # the report lists only some of this line's components
            continue
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
