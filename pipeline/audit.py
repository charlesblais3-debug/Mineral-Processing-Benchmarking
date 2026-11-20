"""Sanity audit of the loaded database: flags values that pass the per-report checks but look wrong
next to the rest of the data. Run after `rebuild`:  python -m pipeline audit

Flags (each is a prompt for a human look, not an automatic rejection):
  outlier     project more than 2 standard errors off the log-log fit of a metric vs throughput
              (initial CAPEX, process-plant CAPEX, site OPEX/t, process OPEX/t), per commodity group when n >= 6
  range       unit cost or intensity outside broad plausibility bounds
  throughput  several throughput facts without phase context that disagree by > 15 %
  derived     values marked derived (listed so a reviewer sees them)
  review      facts/costs the loader set to needs_review
"""
from __future__ import annotations

import json
import math

from pipeline import export_web
from pipeline.db.init_db import connect

METRICS = {"capex": "Initial CAPEX (US$M)", "plant_capex": "Process plant CAPEX (US$M)",
           "site_opex": "Site OPEX (US$/t)", "process_opex": "Process OPEX (US$/t)"}
BOUNDS = {"site_opex": (2, 400), "process_opex": (1, 150)}


def _fit(points):
    xs = [math.log10(x) for x, _ in points]
    ys = [math.log10(y) for _, y in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    a = my - b * mx
    res = [y - (a + b * x) for x, y in zip(xs, ys)]
    se = math.sqrt(sum(r * r for r in res) / max(n - 2, 1))
    return a, b, se, res


def run() -> list[str]:
    data = export_web.build()
    flags: list[str] = []
    projects = data["projects"]
    for key, label in METRICS.items():
        for group in ("all", "Au", "Cu", "Ni", "Fe"):
            pts = [(p, p["metrics"]["tpd"]["value"], p["metrics"][key]["value"]) for p in projects
                   if "tpd" in p["metrics"] and key in p["metrics"] and p["metrics"][key]["value"] > 0
                   and (group == "all" or p["commodity"] == group)]
            if len(pts) < 6:
                continue
            a, b, se, res = _fit([(x, y) for _, x, y in pts])
            for (p, x, y), r in zip(pts, res):
                if abs(r) > 2 * se:
                    flags.append(f"outlier   {p['name']:<28} {label} = {y:,.1f} at {x:,.0f} t/d is "
                                 f"{10 ** r:.2f}x the {group} fit (n={len(pts)}, p.{p['metrics'][key].get('page')})")
        if key in BOUNDS:
            lo, hi = BOUNDS[key]
            for p in projects:
                v = p["metrics"].get(key, {}).get("value")
                if v is not None and not lo <= v <= hi:
                    flags.append(f"range     {p['name']:<28} {label} = {v:,.1f} outside [{lo}, {hi}]")
    con = connect()
    # several throughput facts with no phase/note: only a problem when they disagree (t/d x 365 vs Mt/a, > 15 %)
    by_report: dict[str, list] = {}
    for rid, name, key, v, ctx in con.execute(
            "SELECT f.report_id, p.name, f.parameter_key, f.value_norm, f.context FROM facts f JOIN reports r ON r.id=f.report_id"
            " JOIN projects p ON p.id=r.project_id WHERE f.parameter_key IN ('throughput_tpd','throughput_mtpa') AND f.value_norm IS NOT NULL"):
        if not ctx or ctx in ("{}", ""):
            by_report.setdefault(rid, []).append((name, v * 1e6 / 365 if key == "throughput_mtpa" else v))
    for rid, vals in by_report.items():
        tpds = [v for _, v in vals]
        if len(tpds) > 1 and max(tpds) / min(tpds) > 1.15:
            flags.append(f"throughput {vals[0][0]:<27} throughputs disagree without phase context: "
                         + ", ".join(f"{v:,.0f} t/d" for v in tpds) + f" ({rid})")
    for rid, key, ctx in con.execute("SELECT report_id, parameter_key, context FROM facts WHERE context LIKE '%derived_note%'"):
        flags.append(f"derived   {rid:<40} {key}: {json.loads(ctx).get('derived_note')}")
    for rid, what, n in con.execute(
            "SELECT report_id, 'facts', COUNT(*) FROM facts WHERE review_status='needs_review' GROUP BY report_id UNION ALL "
            "SELECT report_id, 'costs', COUNT(*) FROM cost_items WHERE review_status='needs_review' GROUP BY report_id"):
        flags.append(f"review    {rid:<40} {n} {what} need review")
    return flags
