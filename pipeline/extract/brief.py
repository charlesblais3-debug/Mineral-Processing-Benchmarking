"""Print each key summary table of a parsed report on one line, with its page number.

    python -m pipeline brief <report_id> [extra regex]

Finds table captions (e.g. "Table 1-4: Initial Capital Cost Summary") about capital costs,
operating costs, design criteria, comminution, recoveries and economics, and prints the
caption plus the following cells joined with " ~ ". Table-of-contents pages are skipped.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOPIC = re.compile(r"capital|capex|operating cost|opex|unit cost|design criteria|process design|comminution|work ind|"
                   r"recover|economic|financial|key (project )?(metrics|parameters|results|outcomes)|summary of (the )?project|"
                   r"cash ?flow summary|production summary|mill(ing)? design|grind", re.I)
CAPTION = re.compile(r"^(Table\s+\d+[-.]\d+[a-z]?\s*[:.\-–]?)", re.I)


def brief(report_id: str, extra: str | None = None, width: int = 900) -> str:
    pages = sorted((ROOT / "reports" / report_id / "parsed" / "pages").glob("*.md"))
    rx = re.compile(extra, re.I) if extra else TOPIC
    out, seen = [], set()
    for f in pages:
        text = f.read_text(encoding="utf-8")
        if text.count("....") > 8 or len(re.findall(r"(?m)^Table\s+\d+[-.]\d+", text)) > 12:
            continue  # list of tables / ToC
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        for i, line in enumerate(lines):
            if not CAPTION.match(line):
                continue
            joined = " ~ ".join(lines[i:i + 140])
            head = joined[:160]
            if not rx.search(head) or head in seen:
                continue
            seen.add(head)
            out.append(f"[p{int(f.stem)}] {joined[:width]}")
    return "\n\n".join(out)
