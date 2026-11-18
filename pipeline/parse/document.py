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
    f = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", f)   # zero-width / direction marks
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

NUMBERED = re.compile(r"^(?:item|section|chapter)?\s*(\d{1,2})(?:\.0)?\.?\s+([A-Za-z].{2,})$", re.I)
PAGE_NUM = re.compile(r"^\s*(page\s+)?([\divxlc]{1,4}|\d{1,2}-\d{1,3})\s*$", re.I)
LONG_SPLIT = re.compile(r"(?<=\S)\s+(?=\d{1,2}(?:\.\d{1,2}){0,2}\.?\s+[A-Z])")
BARE_NUM = re.compile(r"^(?:item|section|chapter)?\s*\d{1,2}(?:\.0)?\.?$", re.I)


def _lines(page: str) -> list[str]:
    out = []
    for line in page.splitlines():
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) > 2:          # a table row (e.g. QP responsibility table), not a heading
            out.append(line.strip())
            continue
        line = " ".join(cells)
        if len(line) > 300:  # badly structured HTML: whole page on one line
            out.extend(x.strip() for x in LONG_SPLIT.split(line))
        elif line:
            out.append(line)
    # "17." on one line and "Recovery Methods" on the next -> one heading line
    merged: list[str] = []
    for line in out:
        if merged and BARE_NUM.match(merged[-1]) and not BARE_NUM.match(line) and not PAGE_NUM.match(line):
            merged[-1] = f"{merged[-1]} {line}"
        else:
            merged.append(line)
    return merged


def _title_like(title: str) -> bool:
    """Short, heading-shaped text (used to decide where a chapter ends)."""
    if len(title) > 90 or title.endswith((".", ",", ";", ":")):
        return False
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", title) if len(w) > 3]
    return bool(words) and (title.isupper() or sum(w[0].isupper() for w in words) / len(words) >= 0.5)


def _is_toc(title: str, nxt: str) -> bool:
    head = title[:150]
    return bool(re.search(r"\.{4,}|_{4,}", head) or re.search(r"\s\d{1,4}$", title) or PAGE_NUM.match(nxt or ""))


def _is_toc_page(page: str) -> bool:
    return sum(bool(re.search(SECTIONS[k], page, re.I)) for k in SECTION_ORDER) >= 5


def find_sections(pages: list[str]) -> dict[str, list[int]]:
    """Return {section: [first_page, last_page]} (1-based, inclusive).

    1) Numbered chapter headings ("17 RECOVERY METHODS", "17. | Recovery Methods", "14 Processing and
       recovery methods"), skipping table-of-contents entries.
    2) Fallback for reports without numbers: a line that is exactly the chapter title.
    A section ends before the next higher-numbered chapter or the next found section, whichever is first.
    """
    numbered: list[tuple[int, int, str]] = []
    for pno, page in enumerate(pages, 1):
        if _is_toc_page(page):
            continue
        lines = _lines(page)
        for i, line in enumerate(lines):
            m = NUMBERED.match(line)
            if m and not _is_toc(m.group(2), lines[i + 1] if i + 1 < len(lines) else ""):
                numbered.append((pno, int(m.group(1)), m.group(2)))

    found: dict[str, tuple[int, int | None]] = {}
    for key in SECTION_ORDER:
        pat = re.compile(rf"^{SECTIONS[key]}\b", re.I)
        hit = next(((p, n) for p, n, t in numbered if pat.match(t)), None)
        if hit is None:  # unnumbered fallback
            exact = re.compile(rf"^{SECTIONS[key]}$", re.I)
            for pno, page in enumerate(pages, 1):
                if _is_toc_page(page):
                    continue
                lines = _lines(page)
                if any(exact.match(l) and not PAGE_NUM.match(lines[i + 1] if i + 1 < len(lines) else "x")
                       for i, l in enumerate(lines)):
                    hit = (pno, None)
                    break
        if hit:
            found[key] = hit

    starts = sorted(p for p, _ in found.values())
    result = {}
    for key, (start, num) in found.items():
        # generous end: the next key section's start (over-including a few pages beats truncating)
        ends = [p for p in starts if p > start]
        end = (min(ends) - 1) if ends else len(pages)
        result[key] = [start, max(start, min(end, start + 80))]
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
