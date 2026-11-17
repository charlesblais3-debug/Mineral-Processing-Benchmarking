# Extraction guide (v1)

How a Claude Code session turns one parsed report into `reports/<report_id>/extracted/v1.json`.
The loader (`python -m pipeline load <report_id>`) validates the file and writes the database.

## Rules

1. **Only what the report states.** Never compute, infer or fill in a value. If the report doesn't give it, leave it out.
2. **Every value needs `page` + `quote`.** `page` is the parsed page file number (`parsed/pages/NNNN.md`).
   `quote` is copied *verbatim* from that page, short (one table row or one sentence fragment) but containing the number.
   The loader rejects quotes it can't find on that page.
3. **Read only the mapped sections** in `parsed/sections.json`: summary, testwork, recovery, costs, economics (reserves/mining for scale).
4. **Keep the report's own units and currency.** The loader converts (kWh/st → kWh/t, CAD → USD, escalation).
5. **Prefer LOM / design values** over single-year values. Say which in `statistic`.
6. **Costs: copy each line of the summary cost tables**, using the report's wording in `raw_label`. Mark subtotals `is_subtotal: true` and the grand total `is_total: true`, so the loader can check that the lines add up. A breakdown of one line (e.g. Processing → labour, power, reagents) gets `part_of: "<that line's raw_label>"` and is checked against it. Only set `l1`/`l2` when the automatic mapping (`python -m pipeline map-cost <type> "<label>"`) would get it wrong.
7. **Comminution:** record the measurement details in `context`: `test` (Bond ball mill standard / Bond-equivalent from SMC ...), `closing_screen_um`, `n_samples`, `ore_type`, `lab`. Use `statistic` for mean / p75 / p80 / design.

## File format

```json
{
  "extractor": "claude-code",
  "report": {
    "project": "Lynn Lake", "title": "...", "standard": "NI 43-101", "study_level": "FS",
    "effective_date": "2023-08-22", "primary_commodity": "Au", "by_products": ["Ag"],
    "country": "Canada", "region": "Manitoba", "mine_type": "open_pit", "project_status": "study",
    "currency": "CAD", "cost_base_year": 2022, "qps": ["..."]
  },
  "flowsheet": {"unit_ops": ["primary_gyratory", "sag_mill", "ball_mill", "pebble_crush", "gravity", "cil", "elution_ew", "cyanide_detox"],
                "page": 231, "quote": "..."},
  "facts": [
    {"parameter": "throughput_tpd", "value": 8000, "unit": "t/d", "statistic": "design", "page": 231, "quote": "..."},
    {"parameter": "recovery_lom", "metal": "Au", "value": 91.0, "unit": "%", "statistic": "lom_average", "page": 140, "quote": "..."},
    {"parameter": "bwi_ball", "value": 15.2, "unit": "kWh/t", "statistic": "p80",
     "context": {"test": "Bond ball mill (standard)", "closing_screen_um": 106, "n_samples": 42}, "page": 110, "quote": "..."}
  ],
  "costs": [
    {"cost_type": "initial_capex", "raw_label": "Process Plant", "value": 189.0, "multiplier": "M", "currency": "CAD",
     "basis_unit": "total", "page": 319, "quote": "Process Plant | $189.0 | $189.0"},
    {"cost_type": "initial_capex", "raw_label": "Total Initial Capital", "value": 842.4, "multiplier": "M",
     "is_total": true, "page": 319, "quote": "Total Initial Capital | $842.4"},
    {"cost_type": "opex", "raw_label": "Processing", "value": 15.31, "multiplier": "", "basis_unit": "per_t_milled", "page": 330, "quote": "..."}
  ]
}
```

Valid `parameter` keys and units: `config/parameters.yaml`. Valid unit ops: `config/flowsheet_taxonomy.yaml`.
`cost_type`: `initial_capex` | `sustaining` | `closure` | `opex`. `basis_unit`: see `config/cost_taxonomy.yaml`.
