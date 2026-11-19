"""Find technical-report PDFs on company websites (issuers in config/issuers.yaml).

Polite by design: follows robots.txt, one request every few seconds per site, reads at most
`max_pages` pages per company (home page -> investor/project/report pages -> one level deeper),
and records PDF links in reports/catalog.csv. Nothing is downloaded beyond HTML pages.
"""
from __future__ import annotations

import html
import re
import sys
import urllib.robotparser
from urllib.parse import urljoin, urldefrag, urlparse

from pipeline import catalog
from pipeline.http import PoliteClient
from pipeline.taxonomy import load

FOLLOW = re.compile(r"technical|43-?101|report|feasib|stud(y|ies)|project|investor|disclosure|document|publication|asset|operation", re.I)
STRONG = re.compile(r"technical|43-?101|report|feasib|stud(y|ies)|disclosure", re.I)
SKIP = re.compile(r"\.(jpg|jpeg|png|gif|svg|zip|mp4|xlsx?|docx?)(\?|$)|mailto:|tel:|javascript:|/(fr|es|zh|ja)/|news-?release|press|esg|sustainab|career|contact|privacy", re.I)
PDF_GOOD = re.compile(r"technical|43-?101|ni\s?43|feasibility|pre-?feasibility|\bpfs\b|\bpea\b|preliminary economic|economic assessment|mineral (resource|reserve)", re.I)
PDF_BAD = re.compile(r"presentation|factsheet|fact-sheet|financial|md&a|mda|annual|quarter|\bq[1-4]\b|news|press|sustainab|esg|circular|proxy|aif|agm|policy|charter", re.I)
HREF = re.compile(r"<a\s[^>]*href\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)


def _links(base: str, page: str) -> list[tuple[str, str]]:
    out = []
    for href, text in HREF.findall(page):
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()
        url = urldefrag(urljoin(base, html.unescape(href.strip())))[0]
        if url.startswith("http"):
            out.append((url, text))
    return out


def _same_site(a: str, b: str) -> bool:
    ha, hb = urlparse(a).netloc.lower().removeprefix("www."), urlparse(b).netloc.lower().removeprefix("www.")
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def crawl(client: PoliteClient, issuer: dict, max_pages: int = 25) -> list[dict]:
    site = issuer["site"]
    robots = urllib.robotparser.RobotFileParser()
    try:
        robots.parse(client.get(urljoin(site, "/robots.txt"), timeout=20).decode("utf-8", "ignore").splitlines())
    except Exception:  # noqa: BLE001 - no robots.txt: everything allowed
        robots.parse([])
    allowed = lambda u: robots.can_fetch(client.ua, u)  # noqa: E731

    queue: list[tuple[str, int]] = [(site, 0)]
    seen_pages: set[str] = set()
    found: dict[str, dict] = {}
    while queue and len(seen_pages) < max_pages:
        url, depth = queue.pop(0)
        if url in seen_pages or not allowed(url):
            continue
        seen_pages.add(url)
        try:
            raw = client.get(url, timeout=30)
        except Exception:  # noqa: BLE001
            continue
        if raw[:5] == b"%PDF-":
            continue
        page = raw.decode("utf-8", "ignore")
        for link, text in _links(url, page):
            label = f"{text} {link.rsplit('/', 1)[-1]}"
            if ".pdf" in link.lower():
                if PDF_GOOD.search(label) and not PDF_BAD.search(label):
                    found.setdefault(link, {"url": link, "source": "company_site", "issuer": issuer["name"],
                                            "commodity": issuer.get("commodity", ""),
                                            "matched_queries": "company_site",
                                            "note": f"title: {text[:150]}"})
            elif _same_site(link, site) and not SKIP.search(link) and link not in seen_pages and depth < 2:
                if (depth == 0 and FOLLOW.search(label)) or (depth == 1 and STRONG.search(label)):
                    # report pages first
                    (queue.insert(0, (link, depth + 1)) if STRONG.search(label) else queue.append((link, depth + 1)))
    return list(found.values())


def run(names: list[str] | None = None, verbose: bool = True) -> dict:
    issuers = [i for i in load("issuers")["issuers"] if i.get("site") and (not names or i["name"] in names)]
    client = PoliteClient(default_interval=load("sources")["polite_crawling"]["min_seconds_between_requests_per_domain"])
    rows = catalog.read()
    stats = {}
    for issuer in issuers:
        try:
            hits = crawl(client, issuer)
        except Exception as e:  # noqa: BLE001 - one bad site must not stop the run
            print(f"  {issuer['name']}: failed ({type(e).__name__})", file=sys.stderr)
            continue
        new = sum(catalog.merge(rows, h) for h in hits)
        stats[issuer["name"]] = {"pdfs": len(hits), "new": new}
        if verbose:
            print(f"  {issuer['name']:<28} pdf links={len(hits):<3} new={new}", flush=True)
        catalog.write(rows)
    return stats
