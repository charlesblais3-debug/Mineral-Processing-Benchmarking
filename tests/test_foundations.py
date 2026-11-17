import json

import pytest

from pipeline import taxonomy
from pipeline.cli import check_config
from pipeline.db.init_db import init_db


def test_config_is_consistent():
    assert check_config() == []


@pytest.mark.parametrize("label,cost_type,expected", [
    ("Area 200 – Grinding", "initial_capex", ("process", "comminution")),
    ("Pre-Strip", "initial_capex", ("mining", "pre_production")),
    ("Tailings Storage Facility", "initial_capex", ("tailings_water", "tailings")),
    ("EPCM", "initial_capex", ("indirects", "epcm")),
    ("Owner's Costs", "initial_capex", ("owners", "owners")),
    ("Contingency", "initial_capex", ("contingency", "contingency")),
    ("230 kV Transmission Line", "initial_capex", ("offsite_infra", "power")),
    ("Grinding media", "opex", ("processing", "media_liners")),
    ("Reagents", "opex", ("processing", "reagents")),
    ("General & Administrative", "opex", ("ga", "ga")),
    ("TC/RC", "opex", ("offsite", "tc_rc")),
    ("Processing", "opex", ("processing", "other")),
    ("Something unheard of", "opex", ("other", "unallocated")),
])
def test_cost_mapping(label, cost_type, expected):
    m = taxonomy.map_cost_label(label, cost_type)
    assert (m.l1, m.l2) == expected


def test_flowsheet_gravity_cil():
    r = taxonomy.derive_flowsheet(["primary_jaw", "sag_mill", "ball_mill", "pebble_crush", "gravity", "cil", "elution_ew"], ["Au"])
    assert r["families"] == ["gravity_cil"]
    assert r["comminution_circuit"] == "sabc"


def test_flowsheet_copper_flotation_hpgr():
    r = taxonomy.derive_flowsheet(["primary_gyratory", "hpgr", "ball_mill", "flotation_bulk", "regrind", "flotation_cleaner"], ["Cu", "Au"])
    assert "flotation_cu" in r["families"]
    assert "flotation_au" not in r["families"]
    assert r["comminution_circuit"] == "hpgr_ball"


def test_flowsheet_unknown_unit_op_rejected():
    with pytest.raises(ValueError):
        taxonomy.derive_flowsheet(["magic_mill"], ["Au"])


def test_db_roundtrip(tmp_path):
    con = init_db(tmp_path / "t.db")
    con.execute("INSERT INTO projects(id, name, primary_commodity) VALUES ('p1', 'Test', 'Au')")
    con.execute("INSERT INTO reports(id, project_id, standard, study_level) VALUES ('r1', 'p1', 'NI 43-101', 'FS')")
    con.execute(
        "INSERT INTO facts(report_id, parameter_key, value, unit, value_norm, unit_norm, statistic, context, page, quote)"
        " VALUES ('r1', 'bwi_ball', 15.2, 'kWh/st', 16.76, 'kWh/t', 'p80', ?, 187, 'BWi of 15.2 kWh/st')",
        (json.dumps({"closing_screen_um": 106}),),
    )
    con.execute("INSERT INTO pages_fts(report_id, page, text) VALUES ('r1', 187, 'HPGR and Jameson cell')")
    assert con.execute("SELECT count(*) FROM pages_fts WHERE pages_fts MATCH 'jameson'").fetchone()[0] == 1
    with pytest.raises(Exception):
        con.execute("INSERT INTO reports(id, study_level) VALUES ('r2', 'not-a-level')")
    init_db(tmp_path / "t.db")  # re-running is safe
