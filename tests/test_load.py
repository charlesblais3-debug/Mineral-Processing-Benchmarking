import json

from pipeline.db.init_db import init_db
from pipeline.extract import load


def make_report(tmp_path, monkeypatch, costs, facts):
    monkeypatch.setattr(load, "REPORTS", tmp_path)
    rdir = tmp_path / "r1"
    (rdir / "parsed" / "pages").mkdir(parents=True)
    (rdir / "extracted").mkdir()
    (rdir / "parsed" / "pages" / "0001.md").write_text(
        "Plant Throughput | t/d | 8,000\nProcess Plant | $189.0\nOther | $11.0\nTotal Initial Capital | $200.0\n"
        "BWi design 16.5 kWh/st\nEffective Date: June 1, 2024", encoding="utf-8")
    (rdir / "source.json").write_text(json.dumps({"url": "u", "sha256": "x", "issuer": "I"}))
    (rdir / "extracted" / "v1.json").write_text(json.dumps({
        "report": {"project": "Test Project", "currency": "CAD", "primary_commodity": "Au", "effective_date": "2024-06-01",
                   "effective_date_page": 1, "effective_date_quote": "Effective Date: June 1, 2024"},
        "facts": facts, "costs": costs}))
    return init_db(tmp_path / "t.db")


GOOD_COSTS = [
    {"cost_type": "initial_capex", "raw_label": "Process Plant", "value": 189.0, "page": 1, "quote": "Process Plant | $189.0"},
    {"cost_type": "initial_capex", "raw_label": "Other", "value": 11.0, "page": 1, "quote": "Other | $11.0"},
    {"cost_type": "initial_capex", "raw_label": "Total Initial Capital", "value": 200.0, "is_total": True, "page": 1,
     "quote": "Total Initial Capital | $200.0"},
]


def test_good_report_loads_clean_and_converts_short_tons(tmp_path, monkeypatch):
    con = make_report(tmp_path, monkeypatch, GOOD_COSTS, [
        {"parameter": "throughput_tpd", "value": 8000, "unit": "t/d", "page": 1, "quote": "Plant Throughput | t/d | 8,000"},
        {"parameter": "bwi_ball", "value": 16.5, "unit": "kWh/st", "page": 1, "quote": "BWi design 16.5 kWh/st"},
    ])
    res = load.load_report("r1", con)
    assert res["problems"] == [] and res["facts_quote_verified"] == 2
    vnorm = con.execute("SELECT value_norm FROM facts WHERE parameter_key='bwi_ball'").fetchone()[0]
    assert abs(vnorm - 18.19) < 0.01
    assert con.execute("SELECT value FROM cost_items WHERE raw_label='Process Plant'").fetchone()[0] == 189e6


def test_bad_values_are_flagged(tmp_path, monkeypatch):
    costs = [dict(GOOD_COSTS[0], value=289.0)] + GOOD_COSTS[1:]
    con = make_report(tmp_path, monkeypatch, costs, [
        {"parameter": "throughput_tpd", "value": 9000, "unit": "t/d", "page": 1, "quote": "Plant Throughput | t/d | 9,000"},
        {"parameter": "bwi_ball", "value": 45, "unit": "kWh/t", "page": 1, "quote": "BWi design 16.5 kWh/st"},
    ])
    problems = " ".join(load.load_report("r1", con)["problems"])
    assert "quote not found" in problems
    assert "outside" in problems
    assert "sum to" in problems
    statuses = {r[0] for r in con.execute("SELECT review_status FROM facts")}
    assert statuses == {"needs_review"}


def test_costs_converted_with_study_fx_and_escalated(tmp_path, monkeypatch):
    con = make_report(tmp_path, monkeypatch, GOOD_COSTS, [
        {"parameter": "exchange_rate", "value": 0.75, "unit": "USD/CAD", "context": {"pair": "USD/CAD"},
         "page": 1, "quote": "Plant Throughput | t/d | 8,000"},
    ])
    con.execute("UPDATE reports SET cost_base_year=2022")
    load.load_report("r1", con)
    con.execute("UPDATE cost_items SET base_year=2022")
    from pipeline.normalize import costs
    costs.run(con, target_year=2022)
    usd, esc = con.execute("SELECT value_usd, value_usd_escalated FROM cost_items WHERE raw_label='Process Plant'").fetchone()
    assert usd == 189e6 * 0.75 and abs(esc - usd) < 1     # same year -> no escalation


def test_escalation_blend_is_weighted_ratio():
    from pipeline.normalize.indices import escalation_factor
    ppi = {"A": {2019: 100, 2025: 150}, "B": {2019: 100, 2025: 110}}
    assert abs(escalation_factor(ppi, {"A": 0.5, "B": 0.5}, 2019, 2025) - 1.30) < 1e-9
    assert escalation_factor(ppi, {"A": 1.0}, 2018, 2025) is None


def test_value_in_quote_forms():
    from pipeline.extract.load import value_in_quote
    assert value_in_quote(85826, "Mining 6.2% $81,017 $4,809 $85,826")
    assert value_in_quote(415.2, "Open Pit Mining 415.2 192.7 607.9")
    assert not value_in_quote(192.7 * 2, "Open Pit Mining 415.2 192.7 607.9")
    assert value_in_quote(2780, "an after-tax NPV of $2,780M at 5%")
    assert value_in_quote(2040, "an NPV ( 8 % ) of US $ 2 , 040 million")
    assert value_in_quote(2040, "is US$2.04 billion")          # x1000 scale change
    assert value_in_quote(97.5, "Recovery 0.975 0.975")          # fraction -> percent
    assert value_in_quote(-18.995, "After-Tax NPV at 5% (18,995) 722,205")
    assert not value_in_quote(34, "a concentrator throughput increase to 33–35 Mt/a")  # midpoint must be marked derived


def test_currency_conflict():
    from pipeline.extract.load import currency_conflict
    assert currency_conflict("USD", "Mining C$/t mined 2.22") == "CAD"
    assert currency_conflict("CAD", "Mining C$/t mined 2.22") is None
    assert currency_conflict("USD", "Process Plant 910.6") is None
    assert currency_conflict("AUD", "AU$ 1,600 M construction") is None


def test_value_in_quote_space_thousands():
    from pipeline.extract.load import value_in_quote
    assert value_in_quote(5455, "Mine 5 455")
    assert value_in_quote(58572, "Direct costs 58 572")
    assert value_in_quote(1.85, "Totals 1.85 107.11 26.56")


def test_multiplier_case_insensitive():
    from pipeline.extract.load import multiplier
    assert multiplier("K") == multiplier("k") == 1e3
    assert multiplier("M") == multiplier("million") == 1e6
    assert multiplier("bn") == 1e9
    assert multiplier("") == 1
    assert multiplier("lakh") is None


def test_normalize_native_units():
    from pipeline.extract.load import normalize
    grade = {"unit": "native"}; price = {"unit": "native_price"}
    assert normalize(grade, 1.5, "g/t", "Au") == (1.5, "g/t")
    assert normalize(grade, 0.1, "oz/st", "Ag")[0] == 3.42857
    assert normalize(grade, 2.3, "%", "Zn") == (2.3, "%")
    assert normalize(price, 25, "USD/oz", "Ag") == (25, "USD/oz")
    v, u = normalize(price, 2645.5, "USD/t", "Zn"); assert u == "USD/lb" and abs(v - 1.2) < 0.001
    assert normalize(price, 1900, "CAD/oz", "Au")[0] is None   # needs FX: left unnormalized
    assert normalize(grade, 30, "%", None)[0] is None
