import copy
import gzip
import json
from pathlib import Path

import pytest

from import_mitra.cod import (
    CODImporter,
    CODRekapReader,
    CODSync,
    column_runs,
    keep_row_positions,
    remap_extra_values,
)
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
    assert result["columns"] == {"fo": 104, "radio": 89, "all": 59}
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
        if "repeatCell" in request:
            assert request["repeatCell"]["fields"] == "userEnteredFormat.numberFormat"
            assert request["repeatCell"]["range"]["startRowIndex"] == 1
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


def test_standalone_cod_job_keeps_dashboard_metric_shape(setup, monkeypatch):
    sync, _, datasets = setup
    monkeypatch.setattr(sync, "refresh_from_rekap", lambda progress, dry_run: {
        **sync.run(datasets, progress, dry_run=dry_run), "sources": len(datasets)})
    result = CODImporter(sync).run(dry_run=True)
    assert result["fo"] == 2 and result["radio"] == 2 and result["total"] == 4
    assert result["cod"]["columns"] == {"fo": 104, "radio": 89, "all": 59}


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


class RekapSourceAPI:
    def __init__(self, config, profile):
        self.source = profile["source_spreadsheet_id"]
        self.config = config
        self.calls, self.stamp_reads = [], 0
        self.change_stamp = False

    def metadata(self, target):
        assert target == self.source
        self.calls.append(target)
        names = [s["title"] for s in self.config["sources"]] + ["Kontrol"]
        return {"sheets": [{"properties": {"title": title}} for title in names]}

    def read(self, target, a1):
        assert target == self.source  # any direct call to a partner's original file fails the test
        self.calls.append(target)
        if a1 == "'Kontrol'!A1:B8":
            self.stamp_reads += 1
            stamp = "NEW" if self.change_stamp and self.stamp_reads > 1 else "11/11/2026 09:00:00"
            return [["TERAKHIR SELESAI", stamp]]
        if a1 == "'bto'":
            return [["MITRA", "SITE ID", "SP", "REG", "Customer OLO"],
                    ["BTO", "F1", "SP1", "TREG1", "Original extra field"]]
        assert a1 == "'pst_radio'"
        return [["MITRA", "NE ID", "HOP", "WO/SP"], ["PST_RADIO", "R1", "H1", "SP1"]]


def test_cod_reader_only_queries_the_previously_created_rekap_file():
    config = load_config(ROOT / "config/mitra.json")
    config["sources"] = [config["sources"][0], config["sources"][27]]
    profile = json.loads((ROOT / "config/cod2026.json").read_text())
    api = RekapSourceAPI(config, profile)
    reader = CODRekapReader(api, config, profile)
    datasets = reader.collect()
    assert len(datasets) == 2
    assert len(datasets[0].rows) == 1  # source BTO's old start=3 must become 2 in the rekap
    assert "REG" in datasets[0].keys  # preserve fields omitted by the narrower summary tabs
    assert all(d.config["id"] == profile["source_spreadsheet_id"] for d in datasets)
    assert set(api.calls) == {profile["source_spreadsheet_id"]}
    assert reader.stamp == "11/11/2026 09:00:00"


def test_cod_reader_rejects_mixed_refresh_snapshots():
    config = load_config(ROOT / "config/mitra.json")
    config["sources"] = [config["sources"][0], config["sources"][27]]
    profile = json.loads((ROOT / "config/cod2026.json").read_text())
    api = RekapSourceAPI(config, profile)
    api.change_stamp = True
    with pytest.raises(ValueError, match="berubah saat dibaca"):
        CODRekapReader(api, config, profile).collect()


def test_new_rows_grow_grid_before_number_formats(setup):
    sync, api, datasets = setup
    meta, heads, plans, _ = sync.prepare(datasets)
    heads['fo']['properties']['gridProperties']['rowCount'] = 2
    sync.publish(meta, heads, plans, lambda **kwargs: None)
    sid = sync.profile['groups']['fo']['sheet_id']
    grows = [i for i, r in enumerate(api.requests)
             if r.get('updateSheetProperties', {}).get('properties', {}).get('sheetId') == sid]
    formats = [i for i, r in enumerate(api.requests)
               if r.get('repeatCell', {}).get('range', {}).get('sheetId') == sid]
    assert grows and formats and min(grows) < min(formats)


def test_staging_accepts_current_20_million_cell_limit(setup):
    sync, api, datasets = setup
    meta, heads, plans, _ = sync.prepare(datasets)
    meta['sheets'].append({'properties': {'sheetId': 987654321, 'title': 'Large existing tab',
        'gridProperties': {'rowCount': 110000, 'columnCount': 100}}})
    sync.publish(meta, heads, plans, lambda **kwargs: None)
    assert api.writes


def test_staging_still_rejects_workbooks_above_current_limit(setup):
    sync, api, datasets = setup
    meta, heads, plans, _ = sync.prepare(datasets)
    meta['sheets'].append({'properties': {'sheetId': 987654321, 'title': 'Oversized existing tab',
        'gridProperties': {'rowCount': 210000, 'columnCount': 100}}})
    with pytest.raises(ValueError, match='tidak cukup ruang'):
        sync.publish(meta, heads, plans, lambda **kwargs: None)
    assert not api.writes


def test_filter_expands_without_losing_existing_criteria(setup):
    sync, api, datasets = setup
    meta, heads, plans, _ = sync.prepare(datasets)
    original = {'range': {'sheetId': sync.profile['groups']['fo']['sheet_id'],
                         'startRowIndex': 0, 'endRowIndex': 3, 'endColumnIndex': 104},
                'criteria': {'21': {'hiddenValues': ['01.HOLD']}},
                'sortSpecs': [{'dimensionIndex': 1, 'sortOrder': 'ASCENDING'}]}
    heads['fo']['basic_filter'] = copy.deepcopy(original)
    sync.publish(meta, heads, plans, lambda **kwargs: None)
    filters = [r['setBasicFilter']['filter'] for r in api.requests if 'setBasicFilter' in r]
    assert filters == [{**original, 'range': {**original['range'], 'endRowIndex': 10}}]
    assert heads['fo']['basic_filter'] == original


def test_compaction_deletes_whole_rows_descending_and_preserves_anchor(setup):
    from import_mitra.cod import blank_row_runs
    sync, api, datasets = setup
    _, heads, plans, _ = sync.prepare(datasets)
    record = plans['fo']['records'][0]
    plans['fo']['records'] = [None, record, None, None, record, None]
    assert blank_row_runs(plans['fo']['records']) == [(1, 2), (3, 5), (6, 7)]
    result = sync.compact(heads, plans)
    deletes = [r['deleteDimension']['range'] for r in api.requests if 'deleteDimension' in r]
    assert [(r['startIndex'], r['endIndex']) for r in deletes] == [(6, 7), (3, 5), (1, 2)]
    restores = [r['updateCells'] for r in api.requests if 'updateCells' in r]
    assert len(restores) == 1
    assert restores[0]['range']['startColumnIndex'] == 0
    assert 'ARRAYFORMULA' in restores[0]['rows'][0]['values'][0]['userEnteredValue']['formulaValue']
    assert result['fo'] == {'compacted_blank_rows': 4, 'last_row': 3}
    assert plans['fo']['stats']['last_row'] == 3


def test_compaction_keeps_manual_filter_and_bounds_it_to_records(setup):
    sync, api, datasets = setup
    _, heads, plans, _ = sync.prepare(datasets)
    heads['fo']['basic_filter'] = {'range': {'sheetId': sync.profile['groups']['fo']['sheet_id'],
        'startRowIndex': 0, 'endRowIndex': 100, 'endColumnIndex': 104},
        'criteria': {'21': {'hiddenValues': ['01.HOLD']}}}
    sync.compact(heads, plans)
    filters = [r['setBasicFilter']['filter'] for r in api.requests if 'setBasicFilter' in r]
    assert filters[0]['range']['endRowIndex'] == 3
    assert filters[0]['criteria'] == heads['fo']['basic_filter']['criteria']


@pytest.mark.parametrize('has_hole', [False, True])
def test_run_verifies_contiguity_after_native_compaction(setup, monkeypatch, has_hole):
    sync, api, datasets = setup
    prepared = sync.prepare(datasets)
    monkeypatch.setattr(sync, 'prepare', lambda data: prepared)
    monkeypatch.setattr(sync, 'backup', lambda heads: 'backup.json.gz')
    monkeypatch.setattr(sync, 'publish', lambda *args: None)
    monkeypatch.setattr('import_mitra.cod.time.sleep', lambda seconds: None)
    read_ranges = []
    def read_many(target, ranges):
        read_ranges.extend(ranges)
        return [{'values': [['F1'], [], ['NEW']] if has_hole else [['F1'], ['NEW']]},
                {'values': [['R1'], ['R2']]},
                {'values': [['F1'], ['NEW'], ['R1'], ['R2']]}]
    monkeypatch.setattr(api, 'read_many', read_many)
    if has_hole:
        with pytest.raises(ValueError, match='rekonsiliasi'):
            sync.run(datasets)
    else:
        result = sync.run(datasets)
        assert result['fo']['compacted_blank_rows'] == 1
        assert result['fo']['last_row'] == 3
    assert "'Data TIF FO'!B2:B9" in read_ranges


@pytest.mark.parametrize("reconciled", [True, False])
def test_cod_saves_aging_history_only_after_reconciliation(setup, monkeypatch, reconciled):
    sync, api, datasets = setup
    original = api.read_many

    def read_many(target, ranges):
        if len(ranges) == 3 and any("!K2:" in r for r in ranges):
            counts = [2, 2, 4] if reconciled else [1, 2, 4]
            return [{"values": [[f"SITE{i}"] for i in range(n)]} for n in counts]
        return original(target, ranges)

    monkeypatch.setattr(api, "read_many", read_many)
    monkeypatch.setattr("import_mitra.cod.time.sleep", lambda _: None)
    state = sync.state_dir / "aging-state.json"
    if reconciled:
        assert sync.run(datasets)["total"] == 4
        assert state.exists()
    else:
        with pytest.raises(ValueError, match="rekonsiliasi"):
            sync.run(datasets)
        assert not state.exists()
