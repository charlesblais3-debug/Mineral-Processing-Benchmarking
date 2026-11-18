"""Fetch the free FX and escalation series into data/indices/*.csv (tracked) and the DB.

- FX: Bank of Canada Valet API, annual averages (FXA<CCY>CAD), cross-converted to USD per unit.
- Escalation: US BLS Producer Price Index bulk files (download.bls.gov), annual averages.
    CAPEX / sustaining / closure -> 50 % WPU11 (Machinery and equipment, broad)
                                  + 50 % WPUIP2312001 (Inputs to nonresidential construction, goods)
      (Mining machinery WPU1192 alone rose ~75 % 2015-2025, far above plant-cost benchmarks
       such as CEPCI ~45 %; the broad blend tracks those benchmarks closely. WPU1192 is still
       fetched for reference.)
    OPEX                         -> WPUFD4 (Final demand)
Weights are in ESCALATION_FOR; each blend is a weighted average of the component ratios.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from pipeline.http import PoliteClient

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "indices"

PPI_SERIES = {
    "WPU11": "wp.data.12a.Machinery11-113",
    "WPU1192": "wp.data.12c.Machinery117-119",
    "WPUFD4": "wp.data.22.FD-ID",
    "WPUIP2312001": "wp.data.80i.ConstructnInputs",
}
CAPEX_BLEND = {"WPU11": 0.5, "WPUIP2312001": 0.5}
ESCALATION_FOR = {"initial_capex": CAPEX_BLEND, "sustaining": CAPEX_BLEND, "growth": CAPEX_BLEND, "closure": CAPEX_BLEND,
                  "opex": {"WPUFD4": 1.0}}


def escalation_factor(ppi: dict, blend: dict[str, float], from_year: int, to_year: int) -> float | None:
    """Weighted average of index ratios to_year/from_year; None if any component lacks either year."""
    total = 0.0
    for series, w in blend.items():
        s = ppi.get(series, {})
        if from_year not in s or to_year not in s:
            return None
        total += w * s[to_year] / s[from_year]
    return total


CURRENCIES = ["USD", "AUD", "EUR", "GBP", "ZAR", "MXN", "BRL", "CNY", "JPY", "INR"]  # vs CAD


def fetch_ppi(client: PoliteClient) -> dict[str, dict[int, float]]:
    result: dict[str, dict[int, float]] = {}
    for series, filename in PPI_SERIES.items():
        raw = client.get(f"https://download.bls.gov/pub/time.series/wp/{filename}", timeout=300).decode()
        months: dict[int, list[float]] = defaultdict(list)
        for line in raw.splitlines()[1:]:
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) < 4 or parts[0] != series or not parts[2].startswith("M") or parts[2] == "M13":
                continue
            try:
                months[int(parts[1])].append(float(parts[3]))
            except ValueError:
                continue
        result[series] = {y: sum(v) / len(v) for y, v in months.items() if len(v) == 12}
    return result


def fetch_fx(client: PoliteClient) -> dict[str, dict[int, float]]:
    """USD per 1 unit of each currency, annual average."""
    per_cad: dict[str, dict[int, float]] = {}
    for ccy in CURRENCIES:
        try:
            data = json.loads(client.get(f"https://www.bankofcanada.ca/valet/observations/FXA{ccy}CAD/json"))
        except Exception:  # noqa: BLE001 - some currencies have no annual series
            continue
        key = f"FXA{ccy}CAD"
        per_cad[ccy] = {int(o["d"][:4]): float(o[key]["v"]) for o in data["observations"] if o.get(key, {}).get("v")}
    usd = per_cad["USD"]  # CAD per USD
    fx = {"CAD": {y: 1 / v for y, v in usd.items()}, "USD": {y: 1.0 for y in usd}}
    for ccy, series in per_cad.items():
        if ccy != "USD":
            fx[ccy] = {y: v / usd[y] for y, v in series.items() if y in usd}  # (CAD/ccy)/(CAD/USD) = USD/ccy
    return fx


def save(ppi: dict, fx: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "ppi.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["series", "year", "value"])
        for s, years in ppi.items():
            for y, v in sorted(years.items()):
                w.writerow([s, y, round(v, 3)])
    with open(OUT / "fx_usd_per_unit.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["currency", "year", "usd_per_unit"])
        for c, years in sorted(fx.items()):
            for y, v in sorted(years.items()):
                w.writerow([c, y, round(v, 6)])


def load_csv() -> tuple[dict, dict]:
    ppi: dict[str, dict[int, float]] = defaultdict(dict)
    fx: dict[str, dict[int, float]] = defaultdict(dict)
    with open(OUT / "ppi.csv") as f:
        for r in csv.DictReader(f):
            ppi[r["series"]][int(r["year"])] = float(r["value"])
    with open(OUT / "fx_usd_per_unit.csv") as f:
        for r in csv.DictReader(f):
            fx[r["currency"]][int(r["year"])] = float(r["usd_per_unit"])
    return dict(ppi), dict(fx)


def run() -> dict:
    client = PoliteClient(default_interval=1.0)
    ppi, fx = fetch_ppi(client), fetch_fx(client)
    save(ppi, fx)
    return {"ppi": {s: f"{min(v)}-{max(v)}" for s, v in ppi.items()}, "fx": sorted(fx)}
