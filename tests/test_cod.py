import copy
import gzip
import json
from pathlib import Path

import pytest

from import_mitra.cod import CODImporter, CODSync, column_runs, keep_row_positions, remap_extra_values
from import_mitra.mapping import parse_source
from import_mitra.settings import load_config

ROOT = Path(__file__).parents[1]


class CODMemoryAPI:
    def __init__(self, profile):
        self.profile = profile
        self.requests, self.writes = [], []
        self.stages = {}
        self.header_changed = False
        self.values = {
            "fo": {2: [["F1"], ["REMOVED"]], 4: [["SP1"], ["SPX"]], 64: [["TEST"], ["TEST"]]},
            "radio": {10: [["H1"]], 3: [["SP1"]], 1: [["PST_RADIO"]]},
            "all": {1: [["F1"], ["REMOVED"], ["R1"]],
                    3: [["SP1"], ["SPX"], ["SP1"]], 4: [["TEST"], ["TEST"], ["PST_RADIO"]]},
        }

    def metadata(self, target):
        return {"sheets": [{"properties": {"title": s["title"], "sheetId": s["sheet_id"],
            "gridProperties": {"rowCount": 10, "columnCount": len(s["headers"])}}}
            for s in self.profile["groups"].values()] + [{"properties": p} for p in self.stages.values()]}

    def cells(self, target, a1, effective=False):
        key = next(k for k, s in self.profile["groups"].items() if "'" + s["title"] + "'!" in a1)
        spec = self.profile["groups"][key]
        if "!W2:X" in a1:
            return [{"values": [{"userEnteredValue": {"stringValue": value}} for value in row]}
                    for row in [["Testing", "ID-FO"], ["Removed", "ID-REMOVED"], ["Radio", "ID-RADIO"]]]
        headers = [{"effectiveValue": {"stringValue": h}, "userEnteredValue": {"stringValue": h}}
                   for h in spec["headers"]]
        if self.header_changed:
            headers[0]["effectiveValue"]["stringValue"] = "CHANGED"
        templates = [{} for _ in spec["headers"]]
        formulas = {"fo": {1: "=ARRAYFORMULA(B2:B&D2:D)", 2: "=LET(a;Data_TA!A2:A;a)",
                             70: "=TODAY()-K2", 104: '=GEMINI("test";W2)'},
                    "radio": {1: "=LET(a;Data_CMM_ZTE!A2:A;a)",
                              61: '=ARRAYFORMULA(IF(K2:K="";;"OPEN"))', 84: '=XLOOKUP(K2;JPP!J:J;JPP!P:P)'},
                    "all": {}}
        for column, formula in formulas[key].items():
            templates[column - 1] = {"userEnteredValue": {"formulaValue": formula}}
        return [{"values": headers}, {"values": templates}]

    def read_many(self, target, ranges):
        from import_mitra.mapping import col
        result = []
        for a1 in ranges:
            if "_IMCOD_" in a1:
                title = a1.split("'!")[0][1:]
                result.append({"values": [self.uploads[title][0]]})
                continue
            key = next(k for k, s in self.profile["groups"].items() if "'" + s["title"] + "'!" in a1)
            column = next(c for c in self.values[key] if f"!{col(c)}2:" in a1)
            result.append({"values": self.values[key][column]})
        return result

    def write(self, target, a1, rows):
        self.writes.append(a1)
        title = a1.split("'!")[0][1:]
        if not hasattr(self, "uploads"):
            self.uploads = {}
        self.uploads.setdefault(title, []).extend(copy.deepcopy(rows))

    def batch(self, target, requests, **kwargs):
        self.requests.extend(copy.deepcopy(requests))
        for request in requests:
            if "addSheet" in request:
                p = request["addSheet"]["properties"]
                self.stages[p["sheetId"]] = p
            elif "deleteSheet" in request:
                self.stages.pop(request["deleteSheet"]["sheetId"], None)


@pytest.fixture
def setup(tmp_path):
    config = load_config(ROOT / "config/mitra.json")
    profile = json.loads((ROOT / "config/cod2026.json").read_text())
    fo = parse_source([["SITE ID", "SP", "REGIONAL TIF", "NILAI DRM/WO"],
                       ["F1", "SP1", "TREG1", 200], ["NEW", "SP2", "TREG2", 400]],
                      {"title": "data_TEST", "group": "FO", "start": 2}, config["aliases"])
    radio = parse_source([["NE ID", "HOP", "WO/SP", "Program"],
                          ["R1", "H1", "SP1", "NEW INFRA"], ["R2", "H2", "SP2", "NEW INFRA"]],
                         {"title": "pst_radio", "group": "RADIO", "start": 2}, config["aliases"])
    api = CODMemoryAPI(profile)
    return CODSync(api, config, profile, tmp_path), api, [fo, radio]


def test_dry_run_preserves_existing_identity_and_reports_additions(setup):
    sync, api, datasets = setup
    result = sync.run(datasets, dry_run=True)
    assert result["fo"]["retained"] == 1
    assert result["fo"]["added"] == 1
    assert result["fo"]["removed"] == 1
    assert result["columns"] == {"fo": 104, "radio": 89, "all": 58}
    assert result["total"] == 4
    assert not api.requests and not api.writes


def test_cod_write_does_not_touch_header_columns_or_existing_helpers(setup):
    sync, api, datasets = setup
    meta, heads, plans, _summary = sync.prepare(datasets)
    assert plans["fo"]["rows"][1] == [""] * len(plans["fo"]["rows"][1])  # leave removed identity's slot
    assert plans["all"]["rows"] == [["Testing", "ID-FO"], ["", ""], ["Radio", "ID-RADIO"], ["", ""]]
    sync.publish(meta, heads, plans, lambda **kwargs: None)
    groups = {s["sheet_id"]: s for s in sync.profile["groups"].values()}
    for request in api.requests:
        assert "deleteDimension" not in request
        assert "insertDimension" not in request
        assert "repeatCell" not in request
        if "updateSheetProperties" in request:
            assert request["updateSheetProperties"]["fields"] == "gridProperties.rowCount"
        if "updateCells" in request:
            area = request["updateCells"]["range"]
            assert area["startRowIndex"] == 1
            allowed = groups[area["sheetId"]]["managed_columns"]
            assert all(i + 1 in allowed for i in range(area["startColumnIndex"], area["endColumnIndex"]))
        if "copyPaste" in request:
            area = request["copyPaste"]["destination"]
            assert area["startRowIndex"] >= 1
            assert request["copyPaste"]["pasteType"] in {"PASTE_VALUES", "PASTE_FORMULA"}
            if request["copyPaste"]["pasteType"] == "PASTE_FORMULA":
                assert area["startRowIndex"] >= 2
                assert area["startColumnIndex"] != 103  # don't extend paid GEMINI formulas
    assert not api.stages


def test_backups_are_private_and_include_original_formula_cells(setup):
    sync, _, datasets = setup
    _, heads, _, _ = sync.prepare(datasets)
    path = Path(sync.backup(heads))
    assert path.stat().st_mode & 0o777 == 0o600
    with gzip.open(path, "rt") as stream:
        records = [json.loads(line) for line in stream]
    assert records[0]["profile"]["spreadsheet_id"] == sync.target
    assert "ARRAYFORMULA" in json.dumps(records)


def test_header_change_aborts_without_writes(setup):
    sync, api, datasets = setup
    api.header_changed = True
    with pytest.raises(ValueError, match="Judul/urutan"):
        sync.run(datasets, dry_run=True)
    assert not api.requests and not api.writes


def test_duplicate_business_keys_and_mass_mismatch_guard():
    key = ("SITE", "SP", "MITRA")
    records = [{"key": key, "values": [1]}, {"key": key, "values": [2]}]
    slots, result = keep_row_positions(records, [key, key])
    assert [r["values"] for r in slots] == [[1], [2]]
    assert result["added"] == 0
    with pytest.raises(ValueError, match="kunci lama"):
        keep_row_positions(records, [("OTHER", "SP", "M"), ("NO", "SP", "M")])


def test_custom_column_formulas_are_not_silently_moved():
    with pytest.raises(ValueError, match="formula"):
        remap_extra_values([("A",)], [{"values": [{"userEnteredValue": {"formulaValue": "=A2"}}]}],
                          [("A",)], 1)


def test_contiguous_column_copy_ranges():
    assert column_runs([2, 3, 4, 64, 71, 77]) == [(0, 1, 3), (3, 63, 1), (4, 70, 1), (5, 76, 1)]


def test_standalone_cod_job_keeps_dashboard_metric_shape(setup):
    from types import SimpleNamespace
    sync, _, datasets = setup
    reader = SimpleNamespace(collect=lambda progress: datasets)
    result = CODImporter(reader, sync).run(dry_run=True)
    assert result["fo"] == 2 and result["radio"] == 2 and result["total"] == 4
    assert result["cod"]["columns"] == {"fo": 104, "radio": 89, "all": 58}


def test_header_race_after_staging_aborts_before_live_write(setup):
    sync, api, datasets = setup
    meta, heads, plans, _ = sync.prepare(datasets)
    write = api.write

    def changed(*args):
        write(*args)
        api.header_changed = True

    api.write = changed
    with pytest.raises(ValueError, match="Judul/urutan"):
        sync.publish(meta, heads, plans, lambda **kwargs: None)
    assert not any("updateCells" in r for r in api.requests)
    assert not api.stages
