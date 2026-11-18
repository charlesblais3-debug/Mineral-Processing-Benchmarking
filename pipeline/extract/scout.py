"""Print the lines of a parsed report most likely to hold v1 values, with page numbers.

Used by Claude Code sessions (Option A) to find values quickly before writing extracted/v1.json:
    python -m pipeline scout <report_id> [topic ...]
Topics: scale, metallurgy, comminution, design, capex, opex, economics (default: all).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TOPICS = {
    "scale": (["summary", "reserves", "recovery", "economics"],
              r"t/d|tpd|tonnes per day|Mt/a|Mtpa|million tonnes per (year|annum)|mine life|life of mine \(|LOM \(|strip ratio|proven and probable|total (mineral )?reserves?"),
    "metallurgy": (["summary", "testwork", "recovery", "economics"],
                   r"recovery.*%|% .*recovery|head grade|concentrate grade|payable|average annual (gold|copper|nickel|production)"),
    "comminution": (["testwork", "recovery"],
                    r"work index|\bBWi\b|\bRWi\b|\bCWi\b|A ?x ?b|\bAxb\b|abrasion index|\bAi\b|\bDWi\b|\bSMC\b|closing screen|percentile"),
    "design": (["recovery"],
               r"P ?80|availability|installed power| MW\b|kWh/t|specific energy|residence time|SAG mill|ball mill|HPGR|flotation|CIL|CIP|gravity"),
    "capex": (["costs", "summary", "economics"],
              r"initial capital|pre-production capital|total capital|sustaining capital|closure|process plant|contingency|EPCM|indirect|owner'?s cost|direct cost"),
    "opex": (["costs", "summary", "economics"],
             r"operating cost|\$/t (milled|processed|ore)|/t milled|/t processed|processing|G&A|general and admin|mining cost|power|reagents|grinding media|labour|labor|maintenance"),
    "economics": (["economics", "summary"],
                  r"NPV|IRR|payback|exchange rate|US\$/oz|US\$/lb|\$/oz|\$/lb|AISC|all-in sustaining|cash cost|metal price|gold price|copper price|nickel price"),
}


def scout(report_id: str, topics: list[str] | None = None, per_topic: int = 40) -> str:
    folder = ROOT / "reports" / report_id
    sections = json.loads((folder / "parsed" / "sections.json").read_text())
    pages_dir = folder / "parsed" / "pages"
    n_pages = len(list(pages_dir.glob("*.md")))
    out = []
    for topic in topics or list(TOPICS):
        wanted, pattern = TOPICS[topic]
        ranges = [sections[s] for s in wanted if s in sections] or [[1, n_pages]]
        pages = sorted({p for a, b in ranges for p in range(a, b + 1)})
        rx = re.compile(pattern, re.I)
        hits = []
        for p in pages:
            f = pages_dir / f"{p:04d}.md"
            if not f.exists():
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                if rx.search(line) and re.search(r"\d", line) and len(line) < 400:
                    hits.append(f"  p{p:<4} {line.strip()[:220]}")
        out.append(f"== {topic} ({len(hits)} lines; showing {min(len(hits), per_topic)})")
        out.extend(hits[:per_topic])
    return "\n".join(out)
