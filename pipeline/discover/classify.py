"""Classify catalog rows: is this document a technical report, or a news release / AIF / MD&A ...?

Reads only the start of each document (title page + table of contents) and applies rules.
Rows the rules can't decide are marked `unsure`, for a Claude Code session (or you) to settle.
"""
from __future__ import annotations

import html
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from pipeline import catalog
from pipeline.http import PoliteClient

NOT_REPORT = [
    r"annual information form", r"management'?s discussion", r"\bmd&a\b", r"news release", r"press release",
    r"for immediate release", r"consent of", r"financial statements", r"notice of (annual|special)",
    r"management information circular", r"material change report", r"annual report", r"form 20-f",
    r"form 40-f", r"investor presentation", r"letter to shareholders", r"underwriting agreement",
    r"certificate of qualified person",  # stand-alone QP certificate, not the report
    r"\bpresentation\b", r"stock symbol", r"for further information", r"introductory notes", r"interim report", r"quarterly report", r"condensed (consolidated|interim)",
]
IS_REPORT = [
    r"technical report summary", r"ni 43-101 technical report", r"national instrument 43-101",
    r"technical report", r"feasibility study", r"pre-?feasibility", r"preliminary economic assessment",
    r"s-k 1300", r"subpart 1300",
]
LEVELS = [  # first match wins
    ("PFS", r"pre-?feasibility study"),
    ("FS", r"(definitive |bankable )?feasibility study"),
    ("PEA", r"preliminary economic assessment|scoping study"),
    ("resource", r"mineral resource estimate(?!.*reserve)"),
]


def to_text(raw: bytes) -> str:
    t = raw.decode("utf-8", errors="ignore")
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ").replace("\u2019", "'").replace("\u200b", "")
    return re.sub(r"\s+", " ", t).strip()


def classify_text(text: str) -> dict:
    head = text[:1500].lower()
    body = text[:8000].lower()
    neg = [p for p in NOT_REPORT if re.search(p, head)]
    pos = [p for p in IS_REPORT if re.search(p, body)]
    first_pos = min((m.start() for p in IS_REPORT for m in [re.search(p, head)] if m), default=None)
    first_neg = min((m.start() for p in NOT_REPORT for m in [re.search(p, head)] if m), default=None)
    if neg and not (first_pos is not None and first_pos < first_neg):
        status = "not_report"
    elif pos:
        status = "is_report"
    else:
        status = "unsure"
    level = next((lvl for lvl, pat in LEVELS if re.search(pat, body)), "")
    standard = "S-K 1300" if re.search(r"technical report summary|s-k 1300|subpart 1300", body) else (
        "NI 43-101" if re.search(r"43-101", body) else "")
    return {"status": status, "study_level": level, "standard": standard,
            "note": ("neg:" + neg[0]) if status == "not_report" else ""}


def title_snippet(text: str) -> str:
    t = re.sub(r"^(EX-\d+(\.\d+)?\s+\d+\s+\S+\s+)", "", text)  # drop EDGAR exhibit header
    return t[:160]


def classify_row(client: PoliteClient, row: dict) -> dict:
    url = row["url"]
    if url.lower().endswith(".pdf"):
        return {"status": "unsure", "note": "pdf: classify after download"}
    try:
        text = to_text(client.get_head(url))
    except Exception as e:  # noqa: BLE001 - record and move on
        return {"status": "failed", "note": f"fetch: {type(e).__name__}"}
    result = classify_text(text)
    if result["status"] != "not_report":
        result["note"] = (result["note"] + " " if result["note"] else "") + "title: " + title_snippet(text)
    return result


def run(only_status: str = "new", workers: int = 6) -> dict:
    rows = catalog.read()
    todo = [r for r in rows.values() if r["status"] == only_status]
    client = PoliteClient(min_interval={"www.sec.gov": 0.15})  # ~7 req/s, under SEC's 10/s
    counts: dict[str, int] = {}

    def work(r: dict) -> tuple[str, dict]:
        return r["url"], classify_row(client, r)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (url, res) in enumerate(pool.map(work, todo), 1):
            row = rows[url]
            row["status"] = res["status"]
            for k in ("study_level", "standard", "note"):
                if res.get(k):
                    row[k] = res[k]
            counts[res["status"]] = counts.get(res["status"], 0) + 1
            if i % 200 == 0:
                catalog.write(rows)
                print(f"  {i}/{len(todo)} {counts}", file=sys.stderr, flush=True)
    catalog.write(rows)
    return counts
