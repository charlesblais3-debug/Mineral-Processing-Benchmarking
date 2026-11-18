"""Fill value_usd and value_usd_escalated on cost_items.

Currency: the study's own FX assumption when it states one (exchange_rate fact), otherwise the
Bank of Canada annual average for the cost base year.
Escalation: PPI ratio from the cost base year to the target year (default: latest full year).
"""
from __future__ import annotations

import json
import sqlite3

from pipeline.normalize.indices import ESCALATION_FOR, escalation_factor, load_csv


def study_fx(con: sqlite3.Connection, report_id: str, currency: str) -> float | None:
    """USD per 1 unit of `currency` as assumed by the study, if it says so."""
    for value, unit, ctx in con.execute(
            "SELECT value, unit, context FROM facts WHERE report_id=? AND parameter_key='exchange_rate'", (report_id,)):
        pair = (json.loads(ctx or "{}").get("pair") or unit or "").upper().replace(" ", "")
        if pair in (f"USD/{currency}", f"US$/{currency}", f"USD:{currency}", f"USD{currency}"):
            return value                       # e.g. 0.75 USD/CAD -> 1 CAD = 0.75 USD
        if pair in (f"{currency}/USD", f"{currency}:USD", f"{currency}USD"):
            return 1 / value                   # e.g. 1.33 CAD/USD
    return None


def run(con: sqlite3.Connection, target_year: int | None = None) -> dict:
    ppi, fx = load_csv()
    if target_year is None:
        target_year = min(max(v) for v in ppi.values())
    updated = missing = 0
    rows = con.execute(
        "SELECT c.id, c.report_id, c.cost_type, c.value, c.currency, c.base_year, r.effective_date"
        " FROM cost_items c JOIN reports r ON r.id = c.report_id").fetchall()
    fx_cache: dict[tuple[str, str], float | None] = {}
    for cid, rid, ctype, value, ccy, base_year, eff in rows:
        year = base_year or (int(eff[:4]) if eff else None)
        if year and year > target_year:      # newer than the latest index year: treat as current dollars
            year = target_year
        if (rid, ccy) not in fx_cache:
            fx_cache[(rid, ccy)] = 1.0 if ccy == "USD" else study_fx(con, rid, ccy)
        rate = fx_cache[(rid, ccy)] or (fx.get(ccy, {}).get(year) if year else None)
        factor = escalation_factor(ppi, ESCALATION_FOR[ctype], year, target_year) if year else None
        if rate is None or factor is None:
            missing += 1
            continue
        usd = value * rate
        esc = usd * factor
        con.execute("UPDATE cost_items SET value_usd=?, value_usd_escalated=?, escalation_year=? WHERE id=?",
                    (usd, esc, target_year, cid))
        updated += 1
    con.commit()
    return {"updated": updated, "missing_fx_or_year": missing, "target_year": target_year}
