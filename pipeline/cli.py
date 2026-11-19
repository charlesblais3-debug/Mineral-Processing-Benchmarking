"""Command line entry point:  python -m pipeline <command>

Commands
  init-db        create data/benchmark.db
  check-config   validate the YAML taxonomies
  map-cost       try the cost mapper:  python -m pipeline map-cost initial_capex "Grinding area 200"
  discover-edgar find report links on SEC EDGAR -> reports/catalog.csv   [--limit N] [--only q1,q2]
  parse          download + parse one catalog URL:  python -m pipeline parse <url>
  scout          show likely value lines:  python -m pipeline scout <report_id> [capex opex ...]
  export-web     build the web interface (app/web/benchmark.html, app/web/dist/index.html)
  rebuild        recreate data/benchmark.db from every reports/*/extracted/v1.json
  audit          flag curve outliers, implausible unit costs, ambiguous throughputs, derived values
  brief          key summary tables of a report on one line each:  python -m pipeline brief <report_id> [regex]
  load           validate an extraction and load it:  python -m pipeline load <report_id>
  dedupe         mark re-filed copies of the same report as duplicates (keeps latest)
  discover-sites crawl company websites in config/issuers.yaml for report PDFs   [name ...]
  classify       sort catalog rows into is_report / not_report / unsure   [--status new]
"""
from __future__ import annotations

import sys

from pipeline import taxonomy
from pipeline.db.init_db import DEFAULT_DB, init_db


def check_config() -> list[str]:
    problems: list[str] = []
    params = taxonomy.load("parameters")["parameters"]
    for key, p in params.items():
        for field in ("group", "label", "unit", "module"):
            if field not in p:
                problems.append(f"parameters.{key}: missing '{field}'")
        if "range" in p and not (isinstance(p["range"], list) and len(p["range"]) == 2 and p["range"][0] < p["range"][1]):
            problems.append(f"parameters.{key}: bad range {p['range']}")

    flow = taxonomy.load("flowsheet_taxonomy")
    ops = set(flow["unit_ops"])
    for item in flow["families"] + flow["comminution_circuits"]:
        rule = item.get("rule", item)
        for k in ("all", "any", "also_any", "none"):
            for op in rule.get(k, []):
                if op not in ops:
                    problems.append(f"flowsheet {item['key']}: unknown unit op '{op}'")

    costs = taxonomy.load("cost_taxonomy")
    seen: dict[tuple[str, str], str] = {}
    for tree in ("capex", "opex"):
        for l1, node in costs[tree].items():
            for l2, leaf in node.get("l2", {}).items():
                for syn in leaf.get("synonyms", []):
                    s = syn.lower()
                    where = f"{l1}/{l2}"
                    if (tree, s) in seen and seen[(tree, s)] != where:
                        problems.append(f"cost {tree}: synonym '{syn}' in both {seen[(tree, s)]} and {where}")
                    seen[(tree, s)] = where
    return problems


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "init-db":
        init_db().close()
        print(f"database ready: {DEFAULT_DB}")
    elif cmd == "check-config":
        problems = check_config()
        for p in problems:
            print("✗", p)
        print("config OK" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    elif cmd == "discover-edgar":
        from pipeline.discover import edgar
        limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 2000
        only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else None
        print(edgar.run(limit_per_query=limit, only=only))
    elif cmd == "classify":
        from pipeline.discover import classify
        status = argv[argv.index("--status") + 1] if "--status" in argv else "new"
        print(classify.run(only_status=status))
    elif cmd == "parse" and len(argv) == 2:
        from pipeline import catalog
        from pipeline.parse import document
        rows = catalog.read()
        print(document.process(rows.get(argv[1], {"url": argv[1]})))
    elif cmd == "scout" and len(argv) >= 2:
        from pipeline.extract.scout import scout
        print(scout(argv[1], argv[2:] or None))
    elif cmd == "export-web":
        from pipeline import export_web
        for f in export_web.write(export_web.build()):
            print(f"wrote {f} ({f.stat().st_size // 1024} KB)")
    elif cmd == "audit":
        from pipeline import audit
        flags = audit.run()
        for f in flags:
            print(f)
        print(f"{len(flags)} flag(s)")
    elif cmd == "rebuild":
        from pipeline.extract.load import REPORTS, load_report
        DEFAULT_DB.unlink(missing_ok=True)
        con = init_db()
        for f in sorted(REPORTS.glob("*/extracted/v1.json")):
            res = load_report(f.parent.parent.name, con)
            flag = "  ✗ " + "; ".join(res["problems"]) if res["problems"] else ""
            print(f"{res['report']:<48} facts={res['facts']:<3} costs={res['costs']:<3}{flag}")
    elif cmd == "brief" and len(argv) >= 2:
        from pipeline.extract.brief import brief
        print(brief(argv[1], argv[2] if len(argv) > 2 else None))
    elif cmd == "load" and len(argv) == 2:
        from pipeline.extract.load import load_report
        res = load_report(argv[1])
        for p in res.pop("problems"):
            print("  ✗", p)
        print(res)
    elif cmd == "discover-sites":
        from pipeline.discover import company_sites
        print(company_sites.run(argv[1:] or None))
    elif cmd == "dedupe":
        from pipeline.discover import dedupe
        print(dedupe.run())
    elif cmd == "map-cost" and len(argv) == 3:
        print(taxonomy.map_cost_label(argv[2], argv[1]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
