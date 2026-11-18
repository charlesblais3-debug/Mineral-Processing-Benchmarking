from pipeline import catalog
from pipeline.discover import classify, edgar

HIT = {
    "_id": "0001410578-22-000306:thm-20211231xex96d1.htm",
    "_source": {
        "ciks": ["0001134115"], "display_names": ["INTERNATIONAL TOWER HILL MINES LTD  (THM)  (CIK 0001134115)"],
        "file_type": "EX-96.1", "form": "10-K", "file_date": "2022-03-09", "sics": ["1000"],
    },
}


def test_edgar_document_url_and_row():
    assert edgar.document_url(HIT) == "https://www.sec.gov/Archives/edgar/data/1134115/000141057822000306/thm-20211231xex96d1.htm"
    row = edgar.to_row(HIT, "q1")
    assert row["issuer"] == "INTERNATIONAL TOWER HILL MINES LTD  (THM)"
    assert row["exhibit"] == "EX-96.1" and row["cik"] == "1134115"


def test_edgar_keep_filters_exhibits_and_sic():
    sics = {"1000", "1040"}
    assert edgar.keep(HIT, sics)
    assert not edgar.keep({"_source": {**HIT["_source"], "file_type": "10-K"}}, sics)
    assert not edgar.keep({"_source": {**HIT["_source"], "sics": ["7372"]}}, sics)


def test_catalog_merge_and_roundtrip(tmp_path):
    rows: dict = {}
    assert catalog.merge(rows, {"url": "u1", "issuer": "A", "matched_queries": "q1"})
    assert not catalog.merge(rows, {"url": "u1", "issuer": "B", "matched_queries": "q2"})
    assert rows["u1"]["issuer"] == "A"                    # never overwrites
    assert rows["u1"]["matched_queries"] == "q1;q2"        # but unions queries
    assert rows["u1"]["status"] == "new"
    path = tmp_path / "c.csv"
    catalog.write(rows, path)
    assert catalog.read(path)["u1"]["matched_queries"] == "q1;q2"


def test_classify_rules():
    c = classify.classify_text
    assert c("Exhibit 99.1 NI 43-101 Technical Report on the Reko Diq Project")["status"] == "is_report"
    assert c("Exhibit 99.1 Annual Information Form for the year ended 2015 ... feasibility study")["status"] == "not_report"
    assert c("NEWS RELEASE Company announces positive feasibility study")["status"] == "not_report"
    r = c("S-K 1300 Technical Report Summary Pre-Feasibility Study Copper Project")
    assert (r["status"], r["standard"], r["study_level"]) == ("is_report", "S-K 1300", "PFS")
    assert c("Exhibit 99.1 Stock Symbol: AEM For further information")["status"] == "not_report"
    assert c("Exhibit 99.3 Management's Discussion and Analysis")["status"] == "not_report"
    assert c("Exhibit 99.1 Kirkland Lake Gold drill results")["status"] == "unsure"


def test_html_to_text():
    assert classify.to_text(b"<p>Gold&nbsp;<b>CIL</b></p><script>x()</script>") == "Gold CIL"


def test_find_sections_skips_toc_and_ends_before_next_key_section():
    from pipeline.parse.document import find_sections
    pages = [
        "TABLE OF CONTENTS\n13 MINERAL PROCESSING AND METALLURGICAL TESTING\n103\n17 RECOVERY METHODS\n231",
        "13 MINERAL PROCESSING AND METALLURGICAL TESTING\nBond work index tests were ...",
        "13.2 Comminution\nmore testwork",
        "17 RECOVERY METHODS\nThe plant comprises ...",
        "18 PROJECT INFRASTRUCTURE\nroads",
        "21 CAPITAL AND OPERATING COSTS\nTable 21-1",
    ]
    s = find_sections(pages)
    assert s["testwork"] == [2, 3]
    assert s["recovery"] == [4, 5]       # generous: runs up to the next key section (costs)
    assert s["costs"][0] == 6


def test_find_sections_handles_split_and_table_headings():
    from pipeline.parse.document import find_sections
    pages = [
        "17 | Recovery Methods | J. Smith | Alamos",        # QP table row: not a heading
        "17.\nRecovery Methods\nThe plant ...",              # number and title on separate lines
        "21. | Capital and Operating Costs\nTable 21-1",     # number in its own table cell
    ]
    s = find_sections(pages)
    assert s["recovery"][0] == 2 and s["costs"][0] == 3


def test_dedupe_title_key_ignores_exhibit_noise():
    from pipeline.discover.dedupe import title_key
    a = {"issuer": "ALAMOS GOLD INC  (AGI)", "note": "title: EX-99.1 Document NI 43-101 Technical Report for the Island Gold Mine, Dubreuilville"}
    b = {"issuer": "ALAMOS GOLD INC  (AGI)", "note": "title: EXHIBIT 99.1 ex991.htm NI 43-101 Technical Report for the Island Gold Mine, Dubreuilville"}
    c = {"issuer": "ALAMOS GOLD INC  (AGI)", "note": "title: EX-99.1 Document NI 43-101 Technical Report for the Mulatos Property"}
    assert title_key(a) == title_key(b) != title_key(c)
