"""Load the YAML taxonomies and apply them.

- map_cost_label(): raw cost line label -> (l1, l2) by synonym match
- derive_flowsheet(): list of unit-op keys -> flowsheet families + comminution circuit
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    with open(CONFIG / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- costs

@dataclass(frozen=True)
class CostMapping:
    l1: str
    l2: str
    method: str          # "synonym" or "unmapped"
    confidence: float
    matched: str | None  # synonym that matched


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def _cost_tree(cost_type: str) -> dict:
    tax = load("cost_taxonomy")
    if cost_type in ("initial_capex", "sustaining", "growth"):
        return tax["capex"]
    if cost_type == "opex":
        return tax["opex"]
    raise ValueError(f"no category tree for cost_type={cost_type!r}")


def map_cost_label(raw_label: str, cost_type: str) -> CostMapping:
    """Longest synonym found in the label wins (so 'grinding media' beats 'grinding').

    Synonyms of 1–3 characters (e.g. 'tc', 'rc') must match as whole words.
    Unmapped labels go to other/unallocated with confidence 0 and should be
    sent to the LLM classifier or the review queue.
    """
    if cost_type == "closure":
        return CostMapping("closure", "closure", "synonym", 1.0, None)
    label = _norm(raw_label)
    best: tuple[int, str, str, str] | None = None
    for l1, node in _cost_tree(cost_type).items():
        for l2, leaf in node.get("l2", {}).items():
            for syn in leaf.get("synonyms", []):
                s = _norm(syn)
                if len(s) <= 3:
                    hit = re.search(rf"(?<![a-z]){re.escape(s)}(?![a-z])", label)
                else:
                    hit = s in label
                if hit and (best is None or len(s) > best[0]):
                    best = (len(s), l1, l2, syn)
    if best is None:
        return CostMapping("other", "unallocated", "unmapped", 0.0, None)
    length, l1, l2, syn = best
    # generic catch-all leaves ("other") get lower confidence than specific ones
    confidence = 0.6 if l2 == "other" else (0.9 if length >= 6 else 0.75)
    return CostMapping(l1, l2, "synonym", confidence, syn)


# ---------------------------------------------------------------- flowsheets

def _rule_matches(rule: dict, ops: set[str], commodities: set[str]) -> bool:
    if not set(rule.get("all", [])) <= ops:
        return False
    for key in ("any", "also_any"):
        if rule.get(key) and not (set(rule[key]) & ops):
            return False
    if set(rule.get("none", [])) & ops:
        return False
    if rule.get("commodity_any") and not (set(rule["commodity_any"]) & commodities):
        return False
    if set(rule.get("commodity_none", [])) & commodities:
        return False
    return True


def derive_flowsheet(unit_ops: list[str], commodities: list[str]) -> dict:
    tax = load("flowsheet_taxonomy")
    unknown = [u for u in unit_ops if u not in tax["unit_ops"]]
    if unknown:
        raise ValueError(f"unknown unit ops (add to flowsheet_taxonomy.yaml): {unknown}")
    ops, comm = set(unit_ops), set(commodities)
    matched = [f for f in tax["families"] if _rule_matches(f["rule"], ops, comm)]
    # a specific tag (e.g. "Gravity + CIL") replaces the general one it contains ("CIL/CIP")
    replaced = {r for f in matched for r in f.get("replaces", [])}
    families = [f["key"] for f in matched if f["key"] not in replaced]
    circuit = next((c["key"] for c in tax["comminution_circuits"] if _rule_matches(c, ops, comm)), None)
    return {"families": families, "comminution_circuit": circuit}
