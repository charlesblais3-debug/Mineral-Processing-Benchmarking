"""Discover technical reports on SEC EDGAR through its official full-text search.

Finds S-K 1300 Technical Report Summaries (Exhibit 96.x) and NI 43-101 reports filed by
cross-listed issuers (usually Exhibit 99.x on 6-K / 40-F), and records their links in
reports/catalog.csv. Nothing is downloaded here.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from pipeline import catalog
from pipeline.http import PoliteClient
from pipeline.taxonomy import load

SEARCH = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
PAGE = 100
MAX_FROM = 9900  # EDGAR full-text search returns at most 10,000 hits per query

# Exhibits that can hold a technical report. EX-99.x also holds news releases; the
# classification step sorts those out.
EXHIBIT_RE = re.compile(r"^EX-(96|99)(\.\d+)?$")


def document_url(hit: dict) -> str:
    adsh, filename = hit["_id"].split(":", 1)
    src = hit["_source"]
    cik = int(src["ciks"][0])
    return f"{ARCHIVE}/{cik}/{adsh.replace('-', '')}/{filename}"


def keep(hit: dict, mining_sics: set[str]) -> bool:
    src = hit["_source"]
    if not EXHIBIT_RE.match(src.get("file_type", "")):
        return False
    sics = set(src.get("sics") or [])
    return not sics or bool(sics & mining_sics)


def to_row(hit: dict, query_name: str) -> dict:
    src = hit["_source"]
    issuer = (src.get("display_names") or [""])[0]
    issuer = re.sub(r"\s+\(CIK \d+\)$", "", issuer).strip()
    return {
        "url": document_url(hit),
        "source": "edgar",
        "issuer": issuer,
        "cik": str(int(src["ciks"][0])),
        "form": src.get("form", ""),
        "exhibit": src.get("file_type", ""),
        "filed": src.get("file_date", ""),
        "sic": ";".join(src.get("sics") or []),
        "matched_queries": query_name,
    }


def search(client: PoliteClient, q: str, start: str | None = None, end: str | None = None, limit: int = 2000):
    params = f"q={quote(q)}"
    if start or end:
        params += f"&dateRange=custom&startdt={start or '2001-01-01'}&enddt={end or '2100-01-01'}"
    seen = 0
    for offset in range(0, min(limit, MAX_FROM + PAGE), PAGE):
        data = client.get_json(f"{SEARCH}?{params}&from={offset}")
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            return
        for h in hits:
            yield h
            seen += 1
            if seen >= limit:
                return
        if len(hits) < PAGE:
            return


def run(limit_per_query: int = 2000, start: str | None = None, verbose: bool = True, only: list[str] | None = None) -> dict:
    cfg = load("sources")["edgar"]
    mining_sics = {str(s) for s in cfg["sic_codes"]}
    if start is None:
        start = f"{load('sources')['scope_v1']['report_years_min']}-01-01"
    client = PoliteClient(min_interval={"efts.sec.gov": 1.0 / cfg["max_requests_per_second"]})
    rows = catalog.read()
    stats = {"queries": 0, "hits": 0, "kept": 0, "new": 0}
    for name, q in cfg["queries"].items():
        if only and name not in only:
            continue
        stats["queries"] += 1
        kept = new = hits = 0
        for hit in search(client, q, start=start, limit=limit_per_query):
            hits += 1
            if not keep(hit, mining_sics):
                continue
            kept += 1
            new += catalog.merge(rows, to_row(hit, name))
        stats["hits"] += hits
        stats["kept"] += kept
        stats["new"] += new
        if verbose:
            print(f"  {name:<28} hits={hits:<5} kept={kept:<5} new={new}")
    catalog.write(rows)
    stats["catalog_size"] = len(rows)
    return stats
