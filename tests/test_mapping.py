import copy
from itertools import pairwise
from pathlib import Path

import pytest

from import_mitra.importer import build_tables, value_chunks
from import_mitra.mapping import parse_source, partner_formula, project
from import_mitra.settings import load_config

ROOT = Path(__file__).parents[1]


@pytest.fixture
def config():
    return load_config(ROOT / "config/mitra.json")


def test_real_source_manifest_and_fixed_formats(config):
    assert len(config["sources"]) == 34
    assert sum(s["group"] == "FO" for s in config["sources"]) == 27
    assert {s["title"] for s in config["sources"] if s["group"] == "RADIO"} == {
        "pst_radio", "multireka", "huawei", "cmm_zte", "eid", "aviat", "Telkom infra"}
    assert [len(config["schemas"][k]) for k in ("fo", "radio", "all")] == [51, 57, 19]
    assert "data_HARRIF" not in {s["title"] for s in config["sources"]}


def test_column_reordering_missing_extras_and_duplicates(config):
    s = {"title": "data_TEST", "group": "FO", "start": 2}
    aliases = config["aliases"]
    a = parse_source([["SITE ID", "NILAI DRM/WO", "KETERANGAN UPDATE", "Catatan ekstra"],
                      ["A01", 12000, "=CATATAN", "abaikan"]], s, aliases)
    b = parse_source([["KETERANGAN UPDATE", "Catatan ekstra", "NILAI DRM/WO", "SITE ID"],
                      ["=CATATAN", "abaikan", 12000, "A01"]], s, aliases)
    assert project(a, config["schemas"]["fo"], aliases) == project(b, config["schemas"]["fo"], aliases)
    assert project(a, ["SITE ID", "WITEL", "MITRA", "KETERANGAN UPDATE"], aliases) == [
        ["A01", "", "TEST", "=CATATAN"]]
    c = parse_source([["SITE ID", "WITEL", "WITEL"], ["A01", "Benar", "Duplikat"]], s, aliases)
    assert project(c, ["WITEL"], aliases) == [["Benar"]]


def test_radio_aliases_hop_formula_and_shared_projection(config):
    s = {"title": "pst_radio", "group": "RADIO", "start": 2}
    d = parse_source([["No", "NE ID", "HOP", "NE Name", "Program", "Inhand Date", "Remark", "Nilai WO/SP"],
                      [1, "NE01", "NE01-FE01", "Site Test", "NEW INFRA", 46000, "Catatan", 1200]],
                     s, config["aliases"])
    assert partner_formula(d) == '=ARRAYFORMULA(IF(D2:D<>"","PST_RADIO",""))'
    result = project(d, ["SITE ID", "SITE NAME", "TEMATIK", "TGL ORDER DITERIMA", "MITRA", "FO/Radio"],
                     config["aliases"])
    assert result == [["NE01", "Site Test", "NEW INFRA", 46000, "PST_RADIO", "RADIO"]]
    tables, counts = build_tables([d], config)
    assert counts == {"fo": 0, "radio": 1, "total": 1}
    assert len(next(t for t in tables if t.title == "DATA TIF RADIO").rows[0]) == 57


def test_multirow_headers_blanks_added_rows_and_zero_values(config):
    s = {"title": "ta", "group": "FO", "start": 4}
    data = parse_source([["judul"], [], ["SITE ID", "NILAI DRM/WO"],
                         ["A1", 0], ["", 100], [], ["SITE ID", "NILAI DRM/WO"], ["A2", 12]],
                        s, config["aliases"])
    assert data.rows == [["A1", 0], ["A2", 12]]
    assert partner_formula(data) == '=ARRAYFORMULA(IF(B2:B<>"","TA",""))'


def test_combined_hop_is_beside_site_and_preserves_distinct_radio_links(config):
    aliases = config["aliases"]
    fo = parse_source([["SITE ID", "SP"], ["F1", "SP1"]],
                      {"title": "bto", "group": "FO", "start": 2}, aliases)
    radio = parse_source([["NE ID", "HOP", "WO/SP"],
                          ["R1", "R1-FE1", "SP2"], ["R1", "R1-FE2", "SP2"]],
                         {"title": "pst_radio", "group": "RADIO", "start": 2}, aliases)
    tables, _ = build_tables([fo, radio], config)
    combined = next(t for t in tables if t.title == "DataDisatukan")
    assert combined.headers[3:6] == ["SITE ID", "HOP", "SITE NAME"]
    assert [row[3:5] for row in combined.rows] == [["F1", ""], ["R1", "R1-FE1"], ["R1", "R1-FE2"]]


def test_invalid_header_and_missing_hop_fail_before_publish(config):
    s = {"title": "radio", "group": "RADIO", "start": 2}
    with pytest.raises(ValueError, match="SITE ID"):
        parse_source([["Catatan"], ["nothing"]], s, config["aliases"])
    data = parse_source([["NE ID"], ["A01"]], s, config["aliases"])
    with pytest.raises(ValueError, match="HOP"):
        partner_formula(data)


def test_payload_chunks_respect_utf8_and_keep_every_row():
    rows = [[str(i), "é" * 70] for i in range(20)]
    chunks = list(value_chunks(rows, max_bytes=800, max_cells=8))
    assert [row for _, chunk in chunks for row in chunk] == rows
    assert chunks[0][0] == 1
    for (start, chunk), (following, _) in pairwise(chunks):
        assert following == start + len(chunk)
    with pytest.raises(ValueError, match="payload"):
        list(value_chunks([["x" * 1000]], max_bytes=800))


def test_aliases_do_not_guess_regional(config):
    s = {"title": "data_TEST", "group": "FO", "start": 2}
    data = parse_source([["SITE ID", "REG", "REGIONAL TIF"], ["A1", "AREA 1", "SUMBAGUT"]],
                        s, config["aliases"])
    assert project(data, ["REGIONAL TIF"], config["aliases"]) == [["SUMBAGUT"]]


@pytest.mark.parametrize("group,header,status,expected", [
    ("FO", "Grouping Progress", "02.OGP SURVEY", "02.OGP SURVEY / MATERIAL PREPARATION"),
    ("FO", "STATUS VERIFIKASI COD", "08.RFS", "08.RFS"),
    ("RADIO", "Progress Flag", "06.RFS", "08.RFS"),
    ("RADIO", "Flagging Progress", "03.PERIZINAN", "04.PERIZINAN"),
    ("RADIO", "Flagging Progress", "", ""),
    ("FO", "STATUS VERIFIKASI COD", "STATUS BARU", "STATUS BARU"),
])
def test_combined_flagging_from_group_specific_status(config, group, header, status, expected):
    source = {"title": "test", "group": group, "start": 2}
    data = parse_source([["SITE ID", "HOP", header, "KETERANGAN UPDATE"],
                         ["A1", "A1-B1", status, "Catatan"]], source, config["aliases"])
    headers = config["schemas"]["all"]
    result = project(data, headers, config["aliases"])[0]
    index = headers.index("FLAGGING FO/RADIO")
    assert headers[index + 1] == "KETERANGAN UPDATE"
    assert result[index:index + 2] == [expected, "Catatan"]


def test_reject_target_as_source_and_duplicate_titles(config, tmp_path):
    import json
    bad = copy.deepcopy(config)
    bad["sources"][0]["id"] = config["target_spreadsheet_id"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="tujuan"):
        load_config(path)

