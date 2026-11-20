import copy
import gzip
import json
import re
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from import_mitra.jpp import JPPImport, read_upload
from import_mitra.mapping import col

ROOT = Path(__file__).parents[1]


def excel_fixture(path, rows, title="ALL DATA"):
    """Minimal OOXML fixture; formulas contain explicit cached Excel values."""
    xml_rows = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row, 1):
            if value is None:
                continue
            reference = f"{col(c)}{r}"
            if isinstance(value, tuple):
                formula, cached = value
                cells.append(f'<c r="{reference}" t="str"><f>{escape(formula)}</f><v>{escape(cached)}</v></c>')
            elif isinstance(value, (int, float)):
                cells.append(f'<c r="{reference}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{reference}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    with ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
        z.writestr("xl/workbook.xml", f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{title}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:{col(max(map(len, rows)))}{len(rows)}"/><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
    return path


class JPPMemoryAPI:
    def __init__(self, profile, old_height=5):
        self.profile = profile
        width = len(profile["headers"])
        self.live = [profile["headers"][:], ["OLD"] * (width-1) + ['=J2&"-"&L2']]
        self.live += [["STALE"] * width for _ in range(old_height-2)]
        self.properties = {"sheetId": profile["sheet_id"], "title": profile["title"],
                           "gridProperties": {"rowCount": old_height, "columnCount": width}}
        self.filter = {"range": {"sheetId": profile["sheet_id"], "endRowIndex": old_height}, "criteria": {"3": {}}}
        self.batches, self.stage, self.failure = [], None, ""

    def metadata(self, _target):
        return {"sheets": [{"properties": copy.deepcopy(self.properties), "basicFilter": self.filter}]}

    def cells(self, _target, a1):
        if "!BE" in a1:
            row = int(re.search(r"!BE(\d+)", a1).group(1))-1
            return [{"values": [{"userEnteredValue": {"formulaValue": self.live[row][-1]}}]}]
        first, last = map(int, re.search(r"!A(\d+):[A-Z]+(\d+)", a1).groups())
        result = []
        for row in self.live[first-1:last]:
            cells = []
            for v in row:
                key = "formulaValue" if isinstance(v, str) and v.startswith("=") else "stringValue"
                cells.append({"userEnteredValue": {key: v}})
            result.append({"values": cells})
        return result

    def write(self, _target, a1, rows):
        if self.failure == "upload":
            raise RuntimeError("staging unavailable")
        start = int(re.search(r"!A(\d+)", a1).group(1))-1
        self.stage[start:start+len(rows)] = copy.deepcopy(rows)

    def read(self, _target, a1):
        if "_IMJPP_" in a1:
            if self.failure == "corrupt":
                return [["BROKEN"]]
            return copy.deepcopy(self.stage)
        return copy.deepcopy(self.live[1:])

    def batch(self, _target, requests, **kwargs):
        self.batches.append(copy.deepcopy(requests))
        for request in requests:
            if "addSheet" in request:
                self.stage = []
            elif "deleteSheet" in request:
                self.stage = None
            elif "appendDimension" in request:
                self.properties["gridProperties"]["rowCount"] += request["appendDimension"]["length"]
            elif "updateCells" in request:
                update = request["updateCells"]
                if "rows" not in update:
                    self.live = [self.live[0]]
                else:
                    c = update["range"]["startColumnIndex"]
                    for r, row in enumerate(update["rows"], 1):
                        self.live[r][c] = row["values"][0]["userEnteredValue"]["formulaValue"]
            elif "copyPaste" in request and request["copyPaste"]["pasteType"] == "PASTE_VALUES":
                self.live = [self.live[0]] + copy.deepcopy(self.stage[1:])


@pytest.fixture
def setup(tmp_path):
    profile = json.loads((ROOT / "config/jpp2026.json").read_text())
    settings = SimpleNamespace(jpp_profile=ROOT / "config/jpp2026.json", cod_profile=ROOT / "config/cod2026.json",
                               state_dir=tmp_path, credentials_file=None, requests_per_minute=45)
    headers = ["Site ID/NE", "NIM Order", "Src Data", "Plan Transport", "NO Order", "count concat", "Program Kuota FIXED", "Budget FO"]
    path = excel_fixture(tmp_path / "upload.xlsx", [headers,
        ["001", "0007", "new", ("1+1", "FO"), 1, 0, "New Infra", 0],
        ["001", "0007", "new", "Radio", 2, 1, "New Link", 200]])
    api = JPPMemoryAPI(profile)
    return JPPImport(settings, path, api), api


def test_aliases_blanks_zero_cached_values_and_duplicate_sites(setup):
    importer, _ = setup
    rows, missing = read_upload(importer.path, importer.profile)
    at = {h: i for i, h in enumerate(importer.profile["headers"])}
    assert len(rows) == 2  # Duplicate site/order records must not be silently deduplicated.
    assert rows[0][at["Site ID/NE"]] == "001"
    assert rows[0][at["NIM Order"]] == "0007"
    assert rows[0][at["Plan Transport"]] == "FO"  # cached Excel result, not executable formula
    assert rows[0][at["Jumlah Concat"]] == 0
    assert rows[0][at["Budget FO"]] == 0
    assert rows[0][at["Program Kuota"]] == "New Infra"
    assert rows[0][at["TREG"]] == ""
    assert "TREG" in missing and "NE-FE" not in missing


def test_publish_only_values_below_header_preserves_formula_and_backup(setup):
    importer, api = setup
    old = copy.deepcopy(api.live)
    result = importer.run()
    assert result["total"] == 2
    assert api.live[0] == old[0]
    assert api.live[1][5] == ""  # Old TREG is cleared, as requested.
    assert api.live[-1][-1] == '=J3&"-"&L3'
    assert len(api.live) == 3  # All stale rows cleared.
    assert api.stage is None
    with gzip.open(result["backup"], "rt") as stream:
        next(stream)
        backed_up = json.loads(next(stream))
    assert backed_up["rows"][1]["values"][-1]["userEnteredValue"]["formulaValue"] == old[1][-1]
    assert Path(result["backup"]).stat().st_mode & 0o777 == 0o600
    commits = [batch for batch in api.batches if any("copyPaste" in r for r in batch)]
    assert len(commits) == 1
    assert commits[0][0] == {"clearBasicFilter": {"sheetId": importer.profile["sheet_id"]}}
    assert commits[0][-1]["setBasicFilter"]["filter"]["criteria"] == api.filter["criteria"]
    for batch in api.batches:
        for request in batch:
            assert not {"deleteDimension", "insertDimension", "updateSheetProperties"} & request.keys()
            if "updateCells" in request:
                assert request["updateCells"]["range"]["startRowIndex"] == 1
                assert request["updateCells"]["fields"] == "userEnteredValue"


@pytest.mark.parametrize("failure", ["upload", "corrupt"])
def test_staging_failure_keeps_live_data(setup, failure):
    importer, api = setup
    before = copy.deepcopy(api.live)
    api.failure = failure
    with pytest.raises((RuntimeError, ValueError)):
        importer.run()
    assert api.live == before
    assert api.stage is None


def test_schema_change_rejected_before_backup_or_staging(setup):
    importer, api = setup
    api.live[0][0] = "renamed"
    with pytest.raises(ValueError, match="Header"):
        importer.run()
    assert not api.batches


def test_dry_run_has_no_writes(setup):
    importer, api = setup
    assert importer.run(dry_run=True)["total"] == 2
    assert not api.batches
    assert not (importer.settings.state_dir / "jpp-backups").exists()


def test_growth_appends_only_rows(setup):
    importer, api = setup
    api.properties["gridProperties"]["rowCount"] = 2
    api.live = api.live[:2]
    importer.run()
    growth = [r["appendDimension"] for b in api.batches for r in b if "appendDimension" in r]
    assert growth == [{"sheetId": importer.profile["sheet_id"], "dimension": "ROWS", "length": 1}]


def test_empty_source_is_rejected(setup):
    importer, api = setup
    excel_fixture(importer.path, [["Site ID/NE", "NIM Order", "Src Data", "Plan Transport"]])
    with pytest.raises(ValueError, match="kosong"):
        importer.run()
    assert not api.batches
