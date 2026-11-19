# Mineral Processing Benchmarking

A personal benchmarking database built from public mining technical reports (NI 43-101, S-K 1300, JORC),
covering CAPEX and OPEX by standard category, throughput, metallurgy and comminution, with a clean web interface.
Every number links back to the page of the report it came from.

The full plan is in [`PLAN.md`](PLAN.md).

## Layout

| Folder | What's in it |
|---|---|
| `reports/` | The source reports. **PDFs stay on your computer** (git-ignored); metadata and parsed text are tracked |
| `data/` | `benchmark.db` (SQLite) and exports |
| `app/` | The web interface (static site) |
| `pipeline/` | Discover → download → parse → extract → normalize → validate |
| `config/` | Parameter dictionary, cost categories, flowsheet vocabulary, sources |

## Setup on your computer

You need Python 3.11+, Git and Claude Code (desktop app or CLI).

```bash
git clone https://github.com/charlesblais3-debug/Mineral-Processing-Benchmarking.git
```

```bash
cd Mineral-Processing-Benchmarking
```

```bash
python -m venv .venv
```

Activate it: `source .venv/bin/activate` (macOS/Linux) or `.venv\Scripts\activate` (Windows). Then:

```bash
pip install -e ".[dev]"
```

```bash
python -m pipeline init-db
```

```bash
python -m pipeline check-config
```

```bash
pytest
```

Then open Claude Code in this folder. Discovery, downloads and extraction run there, so the PDFs land in `reports/` on your machine.

## Useful commands

```bash
python -m pipeline map-cost initial_capex "Area 200 - Grinding"
```

## The interface

```bash
python -m pipeline export-web
```

This rebuilds `app/web/dist/index.html` from the database. Open that file in any browser: no server is needed. It has three parts:

- **Explore**: preset charts (initial CAPEX, process plant CAPEX, site and process OPEX per tonne, Bond work index) against throughput, with a fitted power-law curve, filters by commodity, stage and flowsheet, and a sortable project table.
- **Estimate**: describe a project (commodity, flowsheet, t/d, optional BWi) and get peer-based ranges for CAPEX and OPEX, plus the closest comparable projects.
- **Project sheet**: click any project or chart point. Every number has a page reference; click it to see the verbatim quote and open the report.


## Metric definitions

- **Site OPEX (US$/t milled):** mining + processing + G&A + tailings/water lines per tonne milled, summed from the report's own lines, so every project has the same scope (by-product credits, royalties, freight, TC/RC and capitalised-stripping adjustments excluded). When a report gives no such breakdown, its stated total per tonne milled is used and labelled as such.
- **OPEX per tonne product:** the report's stated cash cost per tonne of concentrate / pellets / product (iron ore and similar).
- **Initial CAPEX:** new-build pre-production capital only; expansions, restarts and cost-to-complete are growth capital and are not plotted.
