"""Download one report and turn it into cached, page-numbered text + a section map.

Output (tracked in git, small):
    reports/<report_id>/source.json        link + metadata
    reports/<report_id>/parsed/pages/NNNN.md  one file per page; tables as `a | b | c` lines
    reports/<report_id>/parsed/sections.json  {"processing": [start_page, end_page], ...}
The downloaded original (reports/<report_id>/source.htm|pdf) is git-ignored and can be deleted:
the link in source.json is the permanent reference.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date
from pathlib import Path

from pipeline.http import PoliteClient

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports"

# Section headings. NI 43-101 item numbers / S-K 1300 section numbers, matched by title text.
SECTIONS = {
    "testwork":   r"mineral processing and metallurgical testing",
    "recovery":   r"(processing and )?recovery methods",
    "costs":      r"capital and operating costs",
    "economics":  r"economic analysis",
    "reserves":   r"mineral reserve estimates?",
    "mining":     r"mining methods",
    "summary":    r"(executive )?summary",
}
SECTION_ORDER = ["summary", "testwork", "reserves", "mining", "recovery", "costs", "economics"]

PAGE_BREAK = re.compile(r"(?is)<[^>]+page-break-(?:before|after)\s*:\s*always[^>]*>|<hr[^>]*>")


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-")


def report_id(row: dict) -> str:
    issuer = re.sub(r"\s*\([^)]*\)\s*$", "", row.get("issuer", "")).replace("/FI", "")
    return f"{row.get('filed', 'undated')}_{slug(issuer, 30)}_{slug(row.get('exhibit', 'doc'), 10)}"


# ------------------------------------------------------------------ text extraction

def _html_fragment_to_text(fragment: str) -> str:
    f = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    f = re.sub(r"(?i)</t[dh]\s*>", " | ", f)
    f = re.sub(r"(?i)</tr\s*>|<br\s*/?>|</p\s*>|</div\s*>|</h\d\s*>|</li\s*>", "\n", f)
    f = re.sub(r"<[^>]+>", " ", f)
    f = html.unescape(f).replace("\xa0", " ")
    lines = []
    for line in f.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        line = re.sub(r"(\s*\|\s*)+$", "", line)          # trailing empty cells
        line = re.sub(r"^(\|\s*)+", "", line)
        line = re.sub(r"(\|\s*){2,}", "| ", line)         # runs of empty cells
        if line:
            lines.append(line)
    return "\n".join(lines)


def html_to_pages(raw: bytes) -> list[str]:
    text = raw.decode("utf-8", errors="ignore")
    parts = PAGE_BREAK.split(text)
    pages = [_html_fragment_to_text(p) for p in parts]
    pages = [p for p in pages if p.strip()]
    if len(pages) <= 1:  # no page breaks: fall back to ~3,000-character chunks
        whole = pages[0] if pages else ""
        pages, buf = [], []
        for line in whole.splitlines():
            buf.append(line)
            if sum(len(x) for x in buf) > 3000:
                pages.append("\n".join(buf))
                buf = []
        if buf:
            pages.append("\n".join(buf))
    return pages


def pdf_to_pages(raw: bytes) -> list[str]:
    import pymupdf

    with pymupdf.open(stream=raw, filetype="pdf") as doc:
        return [page.get_text("text") for page in doc]


# ------------------------------------------------------------------ section map

TOP_HEADING = re.compile(r"^\s*(?:section\s+)?(\d{1,2})(?:\.0)?\.?\s+([A-Z][^|]{3,90})$", re.I)
PAGE_NUM = re.compile(r"^\s*(page\s+)?[\divxlc]{1,4}\s*$", re.I)


def _headings(pages: list[str]):
    """Yield (page_no, chapter_no, title) for top-level chapter headings, skipping table-of-contents lines."""
    for pno, page in enumerate(pages, 1):
        lines = page.splitlines()
        for i, line in enumerate(lines):
            m = TOP_HEADING.match(line)
            if not m:
                continue
            title = m.group(2).strip()
            if re.search(r"\.{3,}|\s\d{1,4}$", title):           # "Recovery Methods ..... 231"
                continue
            nxt = next((l for l in lines[i + 1:] if l.strip()), "")
            if PAGE_NUM.match(nxt):                                  # ToC: heading then page number
                continue
            yield pno, int(m.group(1)), title


def find_sections(pages: list[str]) -> dict[str, list[int]]:
    """Return {section: [first_page, last_page]} (1-based, inclusive).

    Uses numbered top-level chapter headings ("17 RECOVERY METHODS", "14.0 Processing and Recovery
    Methods"). A section ends on the page before the next higher-numbered chapter starts.
    """
    heads = list(_headings(pages))
    found: dict[str, tuple[int, int]] = {}
    for key in SECTION_ORDER:
        pat = re.compile(rf"^{SECTIONS[key]}\b", re.I)
        for pno, num, title in heads:
            if pat.search(title) and key not in found:
                found[key] = (pno, num)
                break
    result = {}
    for key, (start, num) in found.items():
        later = [p for p, n, _ in heads if p > start and n > num]
        end = (min(later) - 1) if later else min(start + 60, len(pages))
        result[key] = [start, max(start, end)]
    return dict(sorted(result.items(), key=lambda kv: kv[1][0]))


# ------------------------------------------------------------------ main entry

def process(row: dict, client: PoliteClient | None = None, keep_original: bool = False) -> Path:
    client = client or PoliteClient(min_interval={"www.sec.gov": 0.15})
    rid = row.get("report_id") or report_id(row)
    folder = REPORTS / rid
    raw = client.get(row["url"], timeout=300)
    is_pdf = raw[:5] == b"%PDF-"
    pages = pdf_to_pages(raw) if is_pdf else html_to_pages(raw)

    pages_dir = folder / "parsed" / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for old in pages_dir.glob("*.md"):
        old.unlink()
    for i, text in enumerate(pages, 1):
        (pages_dir / f"{i:04d}.md").write_text(text, encoding="utf-8")
    sections = find_sections(pages)
    (folder / "parsed" / "sections.json").write_text(json.dumps(sections, indent=2), encoding="utf-8")

    meta = {k: row.get(k, "") for k in ("url", "source", "issuer", "cik", "form", "exhibit", "filed",
                                          "standard", "study_level", "project", "commodity")}
    meta |= {"report_id": rid, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
             "format": "pdf" if is_pdf else "html", "pages": len(pages), "parsed_on": date.today().isoformat()}
    (folder / "source.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    if keep_original:
        (folder / ("source.pdf" if is_pdf else "source.htm")).write_bytes(raw)
    return folder
