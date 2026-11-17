# Mineral Processing Benchmarking Tool — Project Plan

## 1. What we're building

A personal benchmarking tool for a mineral processing consultant. It has three parts, each in its own folder:

| Part | Folder | What it is |
|---|---|---|
| **Inputs** | `reports/` | Every source technical report (PDF), filed by commodity/project, each with a `source.json` recording where and when it was downloaded |
| **Database** | `data/` | One SQLite file (`benchmark.db`) holding every extracted value, with the report and page it came from |
| **Interface** | `app/` | A clean web UI to search projects, compare them on charts, view correlations and open the source page for any number |

Between the inputs and the database sits an **automated pipeline** (`pipeline/`) that finds reports, downloads them, reads them, extracts the data, normalizes units and cost categories, and checks the results.

### Guiding principles

1. **Every number has a source.** Each value stores the report, the page number and the exact quote it came from. Click a number in the UI and the PDF opens at that page. This is what makes the data trustworthy.
2. **Read each report once, extract many times.** PDFs are converted to text and tables once and the result is cached. Adding a new field later means re-running a cheap extraction step over the cached text, without re-downloading or re-parsing anything.
3. **Store raw labels, query clean categories.** Each cost line keeps its original wording ("Process Plant – Grinding Area 200") *and* a standard category (`CAPEX › Process Plant › Comminution`). You filter on about 15 clean categories, and the raw label is always there for checking.
4. **Studies and actual operations are kept apart.** A feasibility-study OPEX estimate and a reported actual OPEX from an operating mine are different things. Every value is tagged `estimate` or `actual`.
5. **Start narrow.** Version 1 covers about 40 high-value parameters. The structure makes adding the next 400 cheap.

---

## 2. Getting the data (no manual scraping)

### 2.1 About SEDAR+

SEDAR+'s terms of use **prohibit automated access**, and the site uses bot protection (CAPTCHA and traffic filtering) to enforce this. I won't build something that gets around those protections. It would break their terms, it could get your IP or account blocked, and it would make the tool depend on a cat-and-mouse game that breaks every few weeks.

**You don't need SEDAR+ for coverage.** The same NI 43-101 reports almost always exist elsewhere, in places that allow automated access:

| Source | What's there | Access | Notes |
|---|---|---|---|
| **SEC EDGAR** | S-K 1300 Technical Report Summaries (Exhibit 96) for US registrants since 2021. NI 43-101 reports from cross-listed Canadian companies (40-F / 6-K exhibits) | **Official free API + full-text search.** Bots are explicitly allowed (declare a User-Agent, max 10 requests/s) | The best structured source. Covers most large and mid-tier gold and copper producers |
| **Company websites** | Most TSX/TSXV/ASX issuers post their current NI 43-101 / JORC reports on their "Technical Reports" or "Investors" page | Polite crawler (follows `robots.txt`, rate-limited) | The main source for Canadian juniors. Seeded from issuer lists (below) |
| **Web search API** (Brave Search API or Exa) | Finds report PDFs wherever they're hosted: `"NI 43-101" "feasibility study" copper flotation filetype:pdf` | Paid API, low cost | Fills gaps and discovers new projects automatically |
| **ASX announcements** | JORC-compliant PFS/DFS releases from Australian companies | Company sites first. ASX platform only if its terms allow | JORC reports are less standardized, so extraction quality is lower |
| **Issuer lists** | TSX/TSXV mining issuer lists (published by TMX as Excel), ASX listed-company list, SEC SIC codes 1000/1040/1090 | Public downloads | Used to seed the company-website crawler |

**For a report that only exists on SEDAR+:** the pipeline keeps a "wanted" list of projects it knows about but couldn't get, each with a direct link. You can also drop any PDF you come across into `reports/_inbox/`. The pipeline picks it up, identifies it and processes it automatically. This is optional and only for gap-filling. It isn't needed for the tool to work.

### 2.2 What counts as a trustworthy report (version 1)

Only documents signed by Qualified/Competent Persons go in, tagged by tier:

- **Tier 1:** NI 43-101 Technical Report, S-K 1300 TRS, JORC report with a Table 1
- **Tier 2 (optional, flagged):** study-results news releases, used only when no full report exists yet

Every report records its **standard** (NI 43-101 / S-K 1300 / JORC), **study level** (Scoping-PEA / PFS / FS / Operating mine / Resource-only), **effective date**, **issuer** and **QPs**. Resource-only reports are downloaded but skipped for cost extraction.

---

## 3. Folder structure

```
Mineral-Processing-Benchmarking/
├── reports/                         # INPUTS (PDFs are git-ignored; metadata is tracked)
│   ├── _inbox/                      # drop any PDF here; the pipeline files it
│   ├── gold/
│   │   └── 2024-03_artemis_blackwater_FS/
│   │       ├── report.pdf
│   │       ├── source.json          # URL, retrieved date, sha256, standard, study level…
│   │       └── parsed/              # cached text + tables per page (reused forever)
│   │           ├── pages/0001.md …
│   │           ├── tables/*.csv
│   │           └── sections.json    # map of "Item 17 Recovery Methods" → pages 210–245
│   ├── copper/ …
│   └── nickel/ …
├── data/
│   ├── benchmark.db                 # SQLite: the database
│   └── exports/                     # Excel / CSV / Parquet exports
├── pipeline/
│   ├── discover/                    # EDGAR, website crawler, search API, inbox watcher
│   ├── acquire/                     # download, dedupe (sha256), file into reports/
│   ├── parse/                       # PDF → text/tables, OCR fallback, section detection
│   ├── extract/                     # one "module" per topic (see §5)
│   ├── normalize/                   # units, currency, escalation, cost taxonomy mapping
│   └── validate/                    # sanity rules, totals checks, outlier flags
├── config/
│   ├── parameters.yaml              # the parameter dictionary (what we extract)
│   ├── cost_taxonomy.yaml           # standard CAPEX/OPEX categories + synonyms
│   ├── flowsheet_taxonomy.yaml      # standard unit operations + flowsheet families
│   └── sources.yaml                 # issuer lists, search queries, crawl settings
├── app/
│   ├── api/                         # FastAPI: reads benchmark.db
│   └── web/                         # React UI
└── tests/golden/                    # hand-checked reports used to measure accuracy
```

> **Storage note:** a few hundred reports at 10–200 MB each won't fit in git. `reports/**/report.pdf` is git-ignored and lives on your local disk or a synced drive (OneDrive, Dropbox, S3). Metadata and parsed text *are* small enough to version.

---

## 4. Pipeline, stage by stage

```
discover → acquire → parse → extract → normalize → validate → database → UI
                       ↑ cached         ↑ re-run anytime with new modules
```

### 4.1 Discover
- **EDGAR module:** full-text search for `"technical report summary"` and `"NI 43-101"` in exhibits, filtered to mining SIC codes. Pulls filing metadata through the official submissions API.
- **Website crawler:** for each issuer in the seed lists, finds the investor/technical-report page and collects PDF links whose title or first page matches technical-report patterns. Follows `robots.txt`, sends one request every few seconds per domain, and identifies itself honestly.
- **Search module:** a library of queries in `sources.yaml` (commodity × flowsheet × study level), run on a schedule.
- **Output:** a `candidates` table (URL, guessed project, guessed report type). Nothing is downloaded yet.

### 4.2 Acquire
- Downloads candidates and computes a sha256 hash so duplicates (the same report hosted in three places) are stored once.
- **Classifies the first ~5 pages** (title page, QP certificates, date) with a cheap LLM call to confirm it's a technical report and to capture issuer, project, standard, study level and effective date.
- Files the PDF into `reports/<commodity>/<date>_<issuer>_<project>_<level>/` and writes `source.json`.
- Links reports to a **project** entity, so one project's PEA → PFS → FS → operating reports are grouped. The UI shows the latest one by default.

### 4.3 Parse (done once per report)
- **Docling** (IBM, open-source) for layout-aware text and **table** extraction, with **PyMuPDF** as a fast fallback and **OCRmyPDF/Tesseract** for scanned reports.
- **Section detection.** NI 43-101 reports follow a fixed item structure, which makes extraction much easier:
  - Item 13: Mineral Processing & Metallurgical Testing → comminution, recoveries, test conditions
  - Item 17: Recovery Methods → flowsheet, throughput, design criteria, grind size
  - Item 21: Capital & Operating Costs → CAPEX and OPEX tables
  - Item 22: Economic Analysis → NPV, IRR, prices, payback
  - S-K 1300 has equivalent sections (10, 14, 18, 19). JORC reports are mapped heuristically.
- Saves per-page markdown, tables as CSV, and `sections.json`.
- **Full-text index (SQLite FTS5)** over all pages. From day one you can search "Jameson cell", "HPGR" or "Albion" across every report, before any structured extraction exists.

### 4.4 Extract (LLM-based, schema-driven)
- Each **extraction module** = (target sections) + (a Pydantic schema) + (instructions). Example: the `comminution` module reads Items 13 + 17 and returns a list of test results matching the schema.
- An LLM (Claude via API) reads only the relevant sections, not the whole 500-page report, and returns JSON. **Each value must include the page number and a verbatim quote.** The validator then checks that the quote really appears on that page, which catches made-up values.
- Uses the **Batch API** and **prompt caching** to cut cost substantially for bulk runs. Cost per report is measured on the pilot set before scaling.
- Every value records the **extractor version**, so when a module improves you can re-run it and see what changed.

### 4.5 Normalize
- **Units:** t/d ↔ Mtpa (using stated availability, or 365 d if not stated), **kWh/short ton → kWh/t (×1.1023)**. Many older reports give Bond Work Index in kWh/st, and mixing the two silently skews results by 10%. Also g/t ↔ oz/st, µm, and so on.
- **Currency:** converted to USD at the report's effective date (Bank of Canada / FRED rates, free). The original currency and value are kept.
- **Cost escalation:** costs are restated to a common base year with a selectable index (US BLS Producer Price Index, which is free, by default; CEPCI if you have a subscription). The UI can show both nominal and escalated values.
- **Cost categories:** each raw cost line is mapped to the standard taxonomy (§6) by synonym rules first and an LLM classifier second. The mapping confidence is stored, and low-confidence mappings go to review.
- **Flowsheet:** unit operations are mapped to a controlled vocabulary, and a **flowsheet family** is derived from them (see §6.3).

### 4.6 Validate
- **Range checks:** for example BWi 4–30 kWh/t, recovery 0–100 %, Axb 15–300, process OPEX/t within plausible bounds for the flowsheet.
- **Consistency checks:** cost categories sum to the reported total (±2 %); throughput × LOM ≈ reserve tonnage; specific energy roughly consistent with the Bond equation given F80/P80/BWi.
- **Outliers:** flagged against peers (same commodity + flowsheet).
- **Status per value:** `auto-accepted` / `needs review` / `reviewed` / `rejected`. The UI shows accepted values by default and can include flagged ones on request.
- **Golden set:** about 20 reports spanning commodities and flowsheets, checked once by you through the review screen (confirm or correct each value against the highlighted PDF page). This measures extraction accuracy per field and stops quality from slipping when prompts change. It's quality control, not data entry.

---

## 5. Database design (built to grow)

Fixed tables hold the things that rarely change. A flexible **facts** table holds all parameters, so a new parameter **never needs a schema change**. You add it to `parameters.yaml`, write or extend a module, and re-run.

```
projects      id, name, country, region, lat, lon, primary_commodity, by_products,
              mine_type (OP/UG/both), status (study/construction/operating/closed)

reports       id, project_id, title, issuer, standard, study_level, effective_date,
              publish_date, qps, source_url, file_path, sha256, currency,
              cost_base_year, tier, supersedes_report_id

flowsheets    report_id, family, unit_ops[] (controlled vocab), description_quote, page

facts         id, report_id, parameter_key, value, unit, value_norm, unit_norm,
              statistic (mean|median|P80|design|min|max), basis (estimate|actual),
              context JSON (e.g. test method, sample count, ore type, phase/year),
              page, quote, confidence, extractor_version, review_status

cost_items    id, report_id, cost_type (initial_capex|sustaining|closure|opex),
              category_l1, category_l2, raw_label, value, currency, value_usd,
              value_usd_escalated, basis_unit (total|per_t_milled|per_t_mined|per_oz|per_lb),
              base_year, page, quote, mapping_confidence, review_status

parameters    key, label, group, canonical_unit, allowed_range, description
```

**Bond Work Index example.** The measurement details sit in `context`, so they can be filtered and shown:

```json
{
  "parameter_key": "bwi_ball",
  "value": 15.2, "unit": "kWh/st", "value_norm": 16.8, "unit_norm": "kWh/t",
  "statistic": "P80",
  "context": {
    "test": "Bond ball mill (standard)",        // vs. "Bond-equivalent from SMC/SPI/Levin"
    "closing_screen_um": 106,                   // affects result significantly
    "feed_size": "-3.35 mm (6 mesh)",
    "n_samples": 42, "ore_type": "fresh", "lab": "SGS Lakefield"
  },
  "page": 187, "quote": "...the 80th percentile BWi of 15.2 kWh/st at a 150 mesh closing screen..."
}
```

In the UI, a small badge next to each BWi shows the closing screen and the statistic (mean, P80, design value), so you never compare a P80 design value against an average without knowing it.

---

## 6. Taxonomies (the "no 100 names for the same thing" problem)

These live in YAML, so you can edit them. Each category has a list of synonyms, and anything that can't be mapped goes to `Other` (flagged), never to a new category.

### 6.1 CAPEX (initial)

| Level 1 | Level 2 (as granular as the report allows) |
|---|---|
| **Mining** | Pre-production/pre-strip · Mining equipment · Underground development · Mine infrastructure |
| **Process Plant** | Crushing & ore handling · Grinding/comminution · Concentration (flotation, gravity, leach/CIL) · Dewatering & filtration · Refinery/gold room · Reagents & services · Plant general |
| **Tailings & Water** | TSF / dry stack · Water management |
| **On-site Infrastructure** | Power distribution · Roads · Buildings · Camp |
| **Off-site Infrastructure** | Power line/generation · Access road · Port/rail |
| **Indirects** | EPCM / engineering · Construction indirects · First fills & spares · Commissioning |
| **Owner's Costs** | Owner's team · Pre-production G&A |
| **Contingency** | — |

Kept separate from initial CAPEX: **Sustaining capital** (same L1 split where available) and **Closure/reclamation**.

Reports split these lines differently. Some include indirects inside "Process Plant" and some don't. The tool tracks whether a plant number is **direct-only** or **includes allocated indirects/contingency** and doesn't compare the two blindly.

### 6.2 OPEX

| Level 1 | Level 2 |
|---|---|
| **Mining** | Per t mined and per t milled |
| **Processing** | Labour · Power/energy · Reagents · Grinding media & liners · Maintenance & consumables · Other |
| **G&A / Site** | Including camp and site services |
| **Tailings & Water** | When reported separately |
| **Off-site** | Concentrate transport · Treatment & refining charges · Insurance |
| **Royalties & taxes** | Kept apart, not counted in site OPEX |

Standard bases: **USD/t milled** (primary), USD/t mined (mining), and **USD/oz or USD/lb payable** (cash cost / AISC where reported).

### 6.3 Flowsheet families
Derived from unit operations, so one project can carry several tags:
`Gravity` · `CIL/CIP` · `Gravity + CIL` · `Heap leach (ADR/SX-EW)` · `Flotation (Cu/Cu-Au/Cu-Mo)` · `Flotation + concentrate leach (POX/Albion/BIOX)` · `Ni sulphide flotation` · `Ni laterite (HPAL/RKEF)` · `Polymetallic flotation (Pb-Zn-Ag)` · `Magnetic separation (Fe)` …
Comminution circuit as its own tag: `SABC` · `SAG-Ball` · `HPGR-Ball` · `3-stage crush + ball` · `AG` …

---

## 7. Version 1 parameter list (~40 fields)

| Group | Parameters |
|---|---|
| **Identity** | Project, issuer, country/region, coordinates, commodity (primary + by-products), standard, study level, effective date, mine type, estimate vs actual |
| **Scale** | Nominal throughput (t/d and Mtpa), LOM (years), reserve tonnes & grade |
| **Feed & metallurgy** | LOM head grade (per metal), LOM recovery (per metal), concentrate grade (flotation), payable metal per year |
| **Comminution** | BWi (ball), RWi, CWi, Ai, SMC Axb / DWi. Each with test method, statistic, closing screen and sample count |
| **Process design** | Flowsheet family + unit ops, comminution circuit, primary grind P80, regrind P80 (if any), installed mill power (MW), specific energy (kWh/t) |
| **CAPEX** | Initial total, by L1/L2 category, sustaining total, closure; currency & base year |
| **OPEX** | Total site $/t, by L1/L2 category, cash cost & AISC per payable unit |
| **Economics (context)** | Metal price assumptions, after-tax NPV (and discount rate), IRR, payback |

**Next modules, in rough priority order:** flotation details (cells, residence time, mass pull, reagent types and dosages) · leach details (residence time, NaCN/lime consumption, pre-oxidation) · power & water consumption · gravity recovery (GRG) · consumables breakdown · labour headcount · tailings/dewatering design · mill dimensions & motor sizes.

---

## 8. Correlations & analytics

All of these are calculated live on whatever subset you've filtered to:

- **CAPEX vs throughput:** power-law fit `CAPEX = a · T^b` on log-log axes (the classic six-tenths rule), per flowsheet family and commodity. Shows b, R², n and a **prediction band**, not just a line.
- **Process OPEX ($/t) vs throughput:** economies of scale curve.
- **Specific energy vs BWi**, compared against the Bond equation.
- **Recovery vs head grade**, per commodity/flowsheet.
- **Process plant CAPEX as % of total initial CAPEX**, per flowsheet.
- **Quick estimator:** enter "Gold, Gravity + CIL, 10,000 t/d, BWi 16". You get the peer range (P10/P50/P90) for plant CAPEX, process OPEX/t and specific energy, the regression estimate with its band, and the 10 closest projects. That's useful for the first page of a scoping study or an order-of-magnitude review.

All costs are shown in **escalated USD of a chosen base year** by default, with a toggle back to nominal.

---

## 9. User interface

**Stack:** React + Vite + Tailwind + shadcn/ui (clean, modern components) with **Apache ECharts** for interactive charts and **pdf.js** for the built-in report viewer. **FastAPI** serves the data from `benchmark.db`. It runs locally with one command and can be deployed later (e.g. Fly.io / Render) if you want to share it with clients or colleagues.

(Streamlit would be faster to build but harder to make look polished. We could use it for a throwaway prototype during the pipeline phase and build the real UI in React.)

### Screens (deliberately few)

1. **Home / Search:** one large search box (project, company, region, technology) and a row of filter chips: *Commodity · Flowsheet · Study level · Region · Year · Estimate/Actual*. Results appear as compact cards.
2. **Compare (the main screen):** one large chart. Choose a **preset** ("CAPEX vs Throughput", "OPEX/t vs Throughput", "BWi distribution", "Recovery vs Grade") or pick any X and Y. Colour by commodity or flowsheet; log scale toggle; fitted curve with band. Hover a point for the project name and key values; click it to open the project. A table under the chart lists the same points, with export to Excel.
3. **Project page:** key numbers at the top (throughput, head grade, recovery, CAPEX, OPEX/t), flowsheet badges, a CAPEX/OPEX breakdown bar, a comminution table with method badges, and a report history (PEA → PFS → FS). **Every number is clickable** and opens the PDF at the exact page with the quote highlighted.
4. **Estimator:** the quick-estimate form from §8.
5. **Review (for you only):** a queue of flagged values with the PDF page on the right and accept/correct buttons. Keyboard shortcuts make it quick.

Design rules: lots of white space, one accent colour per commodity, no more than ~5 visible controls per screen, advanced filters folded away, light and dark mode.

---

## 10. Build phases

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Foundations** | Repo scaffold, `parameters.yaml`, `cost_taxonomy.yaml`, `flowsheet_taxonomy.yaml`, DB schema, golden set of ~20 reports chosen | Taxonomies reviewed and agreed by you |
| **1. Acquisition** | EDGAR + website crawler + search API + inbox watcher; dedupe; classification; filing into `reports/` | ~300 Tier-1 reports downloaded across Au/Cu/Ni, with metadata |
| **2. Parsing** | Docling/OCR, section detection, FTS search | Full-text search works across all reports; >90 % of NI 43-101s have Items 13/17/21 located |
| **3. Extraction v1** | Modules: metadata, scale, metallurgy, comminution, flowsheet, CAPEX, OPEX, economics; normalization; validation | Golden-set accuracy measured; ≥95 % on headline numbers (throughput, total CAPEX, OPEX/t, recovery) |
| **4. UI v1** | Search, Compare, Project page with PDF links, Excel export | You can answer "what's typical plant CAPEX for a 5 kt/d CIL?" in under 30 seconds |
| **5. Analytics** | Regressions with bands, estimator, escalation toggle, review queue | Estimator results match your expert expectations on known projects |
| **6. Grow** | Scheduled discovery of new reports, more modules (§7), more commodities (Li, U, Zn, REE, Fe…) | New fields added without schema changes |

Phases 1–2 and the taxonomy work in phase 0 can run in parallel. The UI can start once a small extracted dataset exists. It doesn't need to wait for all reports.

---

## 11. Risks and how we handle them

| Risk | Mitigation |
|---|---|
| LLM makes up or misreads a number | Quote + page required and verified against the page text; range/sum checks; golden-set accuracy tracking; review queue |
| Comparing apples and oranges (indirects in or out, P80 vs mean BWi, nominal vs escalated) | Explicit flags in schema (`statistic`, `includes_indirects`, `basis`, `base_year`), shown as badges in the UI |
| Tables split across pages or scanned images | Docling table reconstruction; OCR fallback; low-confidence values go to review |
| Website layouts change and break the crawler | Several independent sources; search API as backstop; crawler failures logged, not fatal |
| Copyright / redistribution | Reports are public filings and fine for your internal analysis. If the tool is ever shared externally, it links to the original source instead of redistributing PDFs |
| API costs grow | Section-targeted extraction, Batch API, prompt caching, cheaper model for classification. Cost per report tracked |

---

## 12. Decisions needed from you before phase 0

1. **Where should the PDF library live?** Local disk, OneDrive/Dropbox, or cloud storage (S3)? This cloud workspace is temporary, so the reports can't live only here.
2. **Budget for APIs:** an Anthropic API key for extraction and a search API key (Brave or Exa). Both are pay-as-you-go. I'll measure the actual cost per report on the pilot set before any bulk run.
3. **Who uses it:** just you (local app), or shared with colleagues/clients (hosted, with logins)?
4. **Priority commodities and flowsheets** for the first 300 reports (my suggestion: gold CIL/gravity, copper flotation, then nickel sulphide).
5. **Escalation index:** BLS PPI (free) or CEPCI (subscription)?
