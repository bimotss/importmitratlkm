import json
from datetime import date
from pathlib import Path

from import_mitra.aging import (
    AgingTracker,
    grouping_aging_fo,
    grouping_aging_radio,
    is_order_2026,
    parse_date_value,
    to_excel_serial,
)
from import_mitra.cod import project_records
from import_mitra.mapping import parse_source, project
from import_mitra.settings import load_config

ROOT = Path(__file__).parents[1]


def test_date_helpers():
    assert parse_date_value(46231) == date(2026, 7, 28)
    assert parse_date_value(46231.5) == date(2026, 7, 28)
    assert parse_date_value("28/07/2026") == date(2026, 7, 28)
    assert parse_date_value("2026-07-28") == date(2026, 7, 28)
    assert parse_date_value("#N/A") is None
    assert parse_date_value("=XLOOKUP(...)") is None
    assert parse_date_value("") is None
    assert parse_date_value(None) is None

    d = date(2026, 11, 13)
    assert to_excel_serial(d) == 46339
    assert parse_date_value(46339) == d


def test_is_order_2026():
    # 2026 indications
    assert is_order_2026(tahun_project=2026) is True
    assert is_order_2026(tahun_project="2026") is True
    assert is_order_2026(order_date_val=46120) is True  # April 2026
    assert is_order_2026(order_date_val="15/03/2026") is True
    assert is_order_2026(sp_val="TOS TIF 26") is True
    assert is_order_2026(sp_val="TIF CNOP 2026") is True

    # Non-2026 indications
    assert is_order_2026(tahun_project=2025) is False
    assert is_order_2026(tahun_project="2024") is False
    assert is_order_2026(order_date_val="15/03/2025") is False
    assert is_order_2026(order_date_val=45658) is False  # 2025
    assert is_order_2026(sp_val="SP 2025") is False

    # Unspecified fallback
    assert is_order_2026(fallback_true_if_unspecified=True) is True
    assert is_order_2026(fallback_true_if_unspecified=False) is False


def test_grouping_helpers():
    assert grouping_aging_fo(None) == ""
    assert grouping_aging_fo(-1) == ""
    assert grouping_aging_fo(0) == "<1 minggu"
    assert grouping_aging_fo(6) == "<1 minggu"
    assert grouping_aging_fo(7) == "<1 minggu sd <4 minggu"
    assert grouping_aging_fo(28) == "<1 minggu sd <4 minggu"
    assert grouping_aging_fo(29) == ">1 bulan"
    assert grouping_aging_fo(100) == ">1 bulan"

    assert grouping_aging_radio(None) == ""
    assert grouping_aging_radio(-1) == ""
    assert grouping_aging_radio(0) == "≤ 30 Days"
    assert grouping_aging_radio(30) == "≤ 30 Days"
    assert grouping_aging_radio(31) == "> 30 Days"


def test_aging_tracker_lifecycle(tmp_path):
    state_file = tmp_path / "aging-state.json"
    t0 = date(2026, 11, 1)
    tracker = AgingTracker(state_file, today=t0)

    # 1. Cold start with change date
    days, serial, grp = tracker.track_record(
        "radio", ("RAP033-RAP395", "SP1", "PST_RADIO"),
        "06.RFS", is_2026=True, existing_change_date=to_excel_serial(date(2026, 10, 20))
    )
    assert days == 12
    assert serial == to_excel_serial(date(2026, 10, 20))
    assert grp == "≤ 30 Days"

    # 2. Next sync 10 days later with SAME flagging
    t1 = date(2026, 11, 11)
    tracker.today = t1
    days, serial, grp = tracker.track_record(
        "radio", ("RAP033-RAP395", "SP1", "PST_RADIO"),
        "06.RFS", is_2026=True
    )
    assert days == 22
    assert serial == to_excel_serial(date(2026, 10, 20))
    assert grp == "≤ 30 Days"

    # 3. Next sync with CHANGED flagging
    t2 = date(2026, 11, 13)
    tracker.today = t2
    days, serial, grp = tracker.track_record(
        "radio", ("RAP033-RAP395", "SP1", "PST_RADIO"),
        "09.BAST", is_2026=True
    )
    assert days == 0
    assert serial == to_excel_serial(t2)
    assert grp == "≤ 30 Days"

    # Save state and reload
    tracker.save()
    assert state_file.exists()

    tracker2 = AgingTracker(state_file, today=date(2026, 11, 15))
    days2, serial2, _grp2 = tracker2.track_record(
        "radio", ("RAP033-RAP395", "SP1", "PST_RADIO"),
        "09.BAST", is_2026=True
    )
    assert days2 == 2
    assert serial2 == to_excel_serial(t2)


def test_aging_tracker_non_2026():
    tracker = AgingTracker(today=date(2026, 11, 13))
    days, serial, grp = tracker.track_record(
        "fo", ("SITE999", "SP1", "TA"), "08.RFS", is_2026=False
    )
    assert days is None
    assert serial is None
    assert grp == ""


def test_project_integration_with_aging(tmp_path):
    config = load_config(ROOT / "config/mitra.json")
    today = date(2026, 11, 13)
    tracker = AgingTracker(tmp_path / "aging.json", today=today)

    source = {
        "title": "ta",
        "group": "FO",
        "start": 2,
        "header_aliases": {"SUBSTATUS": "STATUS VERIFIKASI COD"},
    }
    headers = [
        "SITE ID", "SP", "TAHUN PROJECT", "TGL ORDER DITERIMA",
        "STATUS VERIFIKASI COD", "Aging Progress", "Grouping Aging Progress"
    ]
    rows = [
        ["SITE-A", "SP-2026", "2026", 46077, "08.RFS", "#N/A", "#N/A"],
        ["SITE-OLD", "SP-2024", "2024", 45000, "08.RFS", "", ""],
    ]
    data = parse_source([headers] + rows, source, config["aliases"])
    projected = project(data, config["schemas"]["fo"], config["aliases"], aging_tracker=tracker)

    schema_fo = config["schemas"]["fo"]
    aging_idx = schema_fo.index("Aging Progress")
    grp_idx = schema_fo.index("Grouping Aging Progress")

    # Row 1 (2026): aging calculated and cleaned from #N/A
    assert isinstance(projected[0][aging_idx], int)
    assert projected[0][aging_idx] == (today - date(2026, 2, 24)).days
    assert projected[0][grp_idx] == ">1 bulan"

    # Row 2 (2024): aging is blank
    assert projected[1][aging_idx] == ""
    assert projected[1][grp_idx] == ""


def test_project_records_cod_integration(tmp_path):
    config = load_config(ROOT / "config/mitra.json")
    cod_cfg = json.loads((ROOT / "config/cod2026.json").read_text())
    today = date(2026, 11, 13)
    tracker = AgingTracker(tmp_path / "aging-cod.json", today=today)

    source = {
        "title": "ta",
        "group": "FO",
        "start": 2,
        "header_aliases": {"SUBSTATUS": "STATUS VERIFIKASI COD"},
    }
    headers = ["SITE ID", "SP", "TAHUN PROJECT", "TGL INHAND", "STATUS VERIFIKASI COD", "Aging Progress"]
    rows = [
        ["SITE-01", "SP#1", 2026, to_excel_serial(date(2026, 10, 1)), "08.RFS", "#N/A"],
        ["SITE-02", "SP#2", 2025, to_excel_serial(date(2025, 10, 1)), "08.RFS", 999],
    ]
    data = parse_source([headers] + rows, source, config["aliases"])

    spec = cod_cfg["groups"]["fo"]
    records, missing = project_records([data], spec, config["aliases"], "FO", aging_tracker=tracker)

    assert len(records) == 2
    # Column 88 in spec is Aging
    col_88_pos = spec["managed_columns"].index(88)

    # 2026 record has calculated aging days
    assert records[0]["values"][col_88_pos] == 43  # 13 Nov - 1 Oct
    # Non-2026 record has empty aging
    assert records[1]["values"][col_88_pos] == ""
    # "Aging" is not in missing
    assert "Aging" not in missing


def test_rekap_and_cod_share_history_and_preserve_source_rows(tmp_path):
    import copy

    config = load_config(ROOT / "config/mitra.json")
    profile = json.loads((ROOT / "config/cod2026.json").read_text())
    source = {"title": "data_PARTNER-FO", "group": "FO", "start": 2}
    data = parse_source([
        ["SITE ID", "SP", "TAHUN PROJECT", "TGL INHAND", "STATUS VERIFIKASI COD",
         "Tanggal Finish Survey", "Aging Progress"],
        ["SITE-01", "SP/01", 2026, "01/10/2026", "02.OGP SURVEY", "10/11/2026", "#N/A"],
    ], source, config["aliases"])
    original = copy.deepcopy(data.rows)
    tracker = AgingTracker(tmp_path / "state.json", today=date(2026, 11, 13))
    rekap = project(data, ["Aging Progress"], config["aliases"], aging_tracker=tracker)
    cod, _ = project_records([data], profile["groups"]["fo"], config["aliases"], "FO", tracker)
    pos = profile["groups"]["fo"]["managed_columns"].index(88)
    assert rekap == [[3]]
    assert cod[0]["values"][pos] == 3
    assert len(tracker.state["fo"]) == 1
    assert data.rows == original
    # Switching from Rekap to standalone COD must keep the same milestone date.
    tracker.save()
    tracker = AgingTracker(tmp_path / "state.json", today=date(2026, 11, 15))
    cod, _ = project_records([data], profile["groups"]["fo"], config["aliases"], "FO", tracker)
    assert cod[0]["values"][pos] == 5
    assert len(tracker.state["fo"]) == 1
    data.rows[0][4] = "08.RFS"
    cod, _ = project_records([data], profile["groups"]["fo"], config["aliases"], "FO", tracker)
    assert cod[0]["values"][pos] == 0


def test_radio_cod_clears_ineligible_and_error_aging():
    config = load_config(ROOT / "config/mitra.json")
    profile = json.loads((ROOT / "config/cod2026.json").read_text())
    data = parse_source([
        ["NE ID", "HOP", "WO/SP", "Inhand Date", "Flagging Progress",
         "Tanggal Ganti Flagging", "Aging Days", "Grouping Aging Progress"],
        ["R1", "H-1", "SP-2025", "01/10/2025", "06.RFS", 46000, 999, "stale"],
        ["R2", "H-2", "SP-2026", "01/10/2026", "#N/A", 46000, "#N/A", "stale"],
        ["R3", "H-3", "SP-2026", "01/10/2026", "06.RFS", 46336, "#N/A", "stale"],
    ], {"title": "data_PST-RADIO", "group": "RADIO", "start": 2}, config["aliases"])
    tracker = AgingTracker(today=date(2026, 11, 13))
    spec = profile["groups"]["radio"]
    records, _ = project_records([data], spec, config["aliases"], "RADIO", tracker)
    indices = [spec["managed_columns"].index(c) for c in (51, 52, 53)]
    assert [[r["values"][i] for i in indices] for r in records] == [
        ["", "", ""], ["", "", ""], [46336, 3, "≤ 30 Days"]]


def test_corrupt_state_and_failed_save_fail_job(tmp_path):
    import pytest

    state = tmp_path / "state.json"
    for content in ('{', '[]', '{"fo": null}', '{"radio": {"key": {}}}'):
        state.write_text(content)
        with pytest.raises((ValueError, TypeError)):
            AgingTracker(state)
    tracker = AgingTracker(tmp_path / "blocked" / "state.json")
    (tmp_path / "blocked").write_text("not a directory")
    with pytest.raises(OSError):
        tracker.save()


def test_date_objects_are_supported():
    from datetime import datetime

    assert parse_date_value(date(2026, 11, 13)) == date(2026, 11, 13)
    assert parse_date_value(datetime(2026, 11, 13, 12, 30)) == date(2026, 11, 13)
