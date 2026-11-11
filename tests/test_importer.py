import copy
import re
from pathlib import Path

import pytest

from import_mitra.importer import Importer
from import_mitra.settings import load_config


class MemoryAPI:
    """Fake API with atomic batches; records whether live tabs were modified."""
    def __init__(self, config):
        self.sheets = {
            10: {"properties": {"sheetId": 10, "title": "DATA TIF FO", "gridProperties": {
                "rowCount": 10, "columnCount": 173}}, "values": [["OLD HEADER"], ["OLD DATA"]]},
            11: {"properties": {"sheetId": 11, "title": "Unrelated", "gridProperties": {
                "rowCount": 2, "columnCount": 2}}, "values": [["KEEP"]]},
        }
        self.sources = {s["id"]: [["NO", "SITE ID", "HOP", "WITEL", "NILAI DRM/WO"],
                                      [1, f"SITE{i}", f"HOP{i}", "Test Witel", 100 + i]]
                        for i, s in enumerate(config["sources"])}
        self.writes, self.batches = [], []
        self.fail_source = None
        self.fail_write = False
        self.fail_commit = False
        self.corrupt_header = False

    def metadata(self, target):
        return {"sheets": [{"properties": copy.deepcopy(s["properties"])} for s in self.sheets.values()]}

    def read(self, spreadsheet_id, a1):
        if spreadsheet_id == self.fail_source:
            raise RuntimeError("403 source access denied")
        return copy.deepcopy(self.sources[spreadsheet_id])

    def write(self, target, a1, values):
        self.writes.append(a1)
        if self.fail_write:
            raise RuntimeError("staging upload failed")
        name, start = re.match(r"'([^']+)'!A(\d+)", a1).groups()
        sheet = next(s for s in self.sheets.values() if s["properties"]["title"] == name)
        while len(sheet["values"]) < int(start) - 1:
            sheet["values"].append([])
        sheet["values"][int(start) - 1:int(start) - 1 + len(values)] = copy.deepcopy(values)

    def read_many(self, target, ranges):
        result = []
        for a1 in ranges:
            name = re.match(r"'([^']+)'!", a1).group(1)
            sheet = next(s for s in self.sheets.values() if s["properties"]["title"] == name)
            if re.search(r"!A2:A\d+$", a1):
                result.append({"values": [[row[0] if row else ""] for row in sheet["values"][1:]]})
            else:
                result.append({"values": [["CORRUPT"]] if self.corrupt_header else [sheet["values"][0]]})
        return result

    def batch(self, target, requests, **kwargs):
        self.batches.append(copy.deepcopy(requests))
        if self.fail_commit and any("copyPaste" in r for r in requests):
            raise RuntimeError("commit validation failed")
        clone = copy.deepcopy(self.sheets)
        for request in requests:
            if "addSheet" in request:
                p = request["addSheet"]["properties"]
                clone[p["sheetId"]] = {"properties": copy.deepcopy(p), "values": []}
            elif "deleteSheet" in request:
                del clone[request["deleteSheet"]["sheetId"]]
            elif "updateCells" in request:
                update = request["updateCells"]
                if "range" in update:
                    area = update["range"]
                    sheet = clone[area["sheetId"]]
                    if set(area) == {"sheetId"}:
                        sheet["values"] = []
                    else:
                        for row in sheet["values"][area.get("startRowIndex", 0):area.get("endRowIndex")]:
                            for c in range(area.get("startColumnIndex", 0), area.get("endColumnIndex", len(row))):
                                if c < len(row):
                                    row[c] = None  # truly absent; distinguish from a copied literal ""
                elif "start" in update:
                    start = update["start"]
                    formula = update["rows"][0]["values"][0]["userEnteredValue"]["formulaValue"]
                    sheet = clone[start["sheetId"]]
                    matrix = sheet["values"]
                    match = re.match(r'=ARRAYFORMULA\(IF\(([A-Z]+)2:[A-Z]+<>"","([^"]+)",""\)\)', formula)
                    if match:
                        key = 0
                        for letter in match[1]:
                            key = key * 26 + ord(letter) - 64
                        if any(row and row[0] is not None for row in matrix[2:]):
                            matrix[1][0] = "#REF!"  # literal empty strings block Google array expansion
                        else:
                            for row in matrix[1:]:
                                row[0] = match[2] if row[key - 1] not in (None, "") else ""
            elif "updateSheetProperties" in request:
                p = request["updateSheetProperties"]["properties"]
                clone[p["sheetId"]]["properties"].update(copy.deepcopy(p))
            elif "copyPaste" in request:
                p = request["copyPaste"]
                clone[p["destination"]["sheetId"]]["values"] = copy.deepcopy(
                    clone[p["source"]["sheetId"]]["values"])
        self.sheets = clone
        return {}


@pytest.fixture
def setup():
    c = load_config(Path(__file__).parents[1] / "config/mitra.json")
    c["sources"] = [dict(s, start=2) for s in [c["sources"][0], c["sources"][27]]]
    api = MemoryAPI(c)
    return c, api, Importer(api, c)


def by_title(api, title):
    return next(s for s in api.sheets.values() if s["properties"]["title"] == title)


def test_staged_commit_preserves_tab_ids_and_unrelated_content(setup):
    _config, api, importer = setup
    result = importer.run()
    assert result["total"] == 2
    assert by_title(api, "DATA TIF FO")["properties"]["sheetId"] == 10
    assert by_title(api, "DATA TIF FO")["properties"]["gridProperties"]["columnCount"] == 51
    assert by_title(api, "DATA TIF RADIO")["properties"]["gridProperties"]["columnCount"] == 57
    assert by_title(api, "DataDisatukan")["properties"]["gridProperties"]["columnCount"] == 17
    assert by_title(api, "Unrelated")["values"] == [["KEEP"]]
    assert not any(s["properties"]["title"].startswith("_IM_STAGING_") for s in api.sheets.values())
    commits = [batch for batch in api.batches if any("copyPaste" in r for r in batch)]
    assert len(commits) == 1  # all outputs published in the same atomic batch
    assert sum("formulaValue" in str(r) for r in commits[0]) == 2
    assert all("deleteSheet" not in r for r in commits[0])  # commit can be retried safely
    importer.run()  # repeat refresh must not create a second MITRA column
    assert by_title(api, "bto")["values"][0].count("MITRA") == 1


@pytest.mark.parametrize("failure", ["read", "upload", "header", "commit"])
def test_failure_does_not_clear_existing_outputs(setup, failure):
    config, api, importer = setup
    if failure == "read":
        api.fail_source = config["sources"][1]["id"]
    elif failure == "upload":
        api.fail_write = True
    elif failure == "header":
        api.corrupt_header = True
    else:
        api.fail_commit = True
    with pytest.raises((RuntimeError, ValueError)):
        importer.run()
    assert by_title(api, "DATA TIF FO")["values"] == [["OLD HEADER"], ["OLD DATA"]]
    assert by_title(api, "Unrelated")["values"] == [["KEEP"]]
    if failure == "read":
        assert api.batches == [] and api.writes == []


def test_dry_run_performs_zero_writes(setup):
    _, api, importer = setup
    result = importer.run(dry_run=True)
    assert result["dry_run"] is True
    assert api.batches == [] and api.writes == []


def test_hourly_cod_hook_reads_rekap_only_after_rekap_publish(setup):
    from types import SimpleNamespace
    _, api, importer = setup
    calls = []

    def refresh(progress, *, dry_run=False):
        assert not dry_run
        assert by_title(api, "DATA TIF FO")["values"][0][0] == "MITRA"
        calls.append("read_rekap")
        return {"preserve_columns": True}

    importer.cod_sync = SimpleNamespace(refresh_from_rekap=refresh)
    result = importer.run()
    assert result["cod"]["preserve_columns"] is True
    assert calls == ["read_rekap"]


def test_partner_array_spill_is_cleared_after_pasting_literal_empty_strings(setup):
    config, api, importer = setup
    for source in config["sources"]:
        api.sources[source["id"]].append([2, "EXTRA-SITE", "EXTRA-HOP", "Witel", 200])
    importer.run()
    assert by_title(api, "bto")["values"][1][0] == "BTO"
    assert by_title(api, "bto")["values"][2][0] == "BTO"
    assert by_title(api, "pst_radio")["values"][2][0] == "PST_RADIO"
    for batch in api.batches:
        for i, request in enumerate(batch):
            if "updateCells" not in request or "start" not in request["updateCells"]:
                continue
            previous = batch[i - 1]["updateCells"]["range"]
            assert previous["startRowIndex"] == 1
            assert previous["startColumnIndex"] == 0 and previous["endColumnIndex"] == 1


def test_bad_partner_names_cannot_be_reported_as_success(setup, monkeypatch):
    _, api, importer = setup
    importer.run()
    by_title(api, "bto")["values"][1][0] = "#REF!"
    monkeypatch.setattr("import_mitra.importer.time.sleep", lambda _: None)
    from import_mitra.importer import build_tables
    datasets = importer.collect(lambda **kwargs: None)
    tables, _ = build_tables(datasets, importer.config)
    with pytest.raises(ValueError, match="Verifikasi kolom MITRA gagal"):
        importer.verify_partner_columns(tables)

