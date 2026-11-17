"""Unit tests untuk MitraValidator di ImportMitra."""

import json

import pytest

from import_mitra.validator import (
    MitraValidator,
    is_new_fo_partner,
    is_yellow_color,
)


class MockAPI:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.read_responses = {}
        self.requests_log = []

    def request(self, method, doc_id, suffix="", params=None, body=None, idempotent=True):
        self.requests_log.append((method, doc_id, params))
        key = (doc_id, tuple(params.get("ranges", [])) if params and "ranges" in params else ())
        if key in self.responses:
            return self.responses[key]
        if doc_id in self.responses:
            return self.responses[doc_id]
        raise ValueError(f"No mock response for doc {doc_id}")

    def read(self, doc_id, a1):
        if (doc_id, a1) in self.read_responses:
            return self.read_responses[(doc_id, a1)]
        if doc_id in self.read_responses:
            return self.read_responses[doc_id]
        return []


def test_is_new_fo_partner():
    assert is_new_fo_partner({"title": "data_ADYAWINSA", "group": "FO"}) is True
    assert is_new_fo_partner({"title": "data_WITEK", "group": "FO"}) is True
    assert is_new_fo_partner({"title": "ta", "group": "FO"}) is False
    assert is_new_fo_partner({"title": "TA", "group": "FO"}) is False
    assert is_new_fo_partner({"title": "bto", "group": "FO"}) is False
    assert is_new_fo_partner({"title": "pst", "group": "FO"}) is False
    # Radio sources should be False
    assert is_new_fo_partner({"title": "pst_radio", "group": "RADIO"}) is False
    assert is_new_fo_partner({"title": "huawei", "group": "RADIO"}) is False


def test_is_yellow_color():
    assert is_yellow_color({"red": 1.0, "green": 1.0, "blue": 0.0}) is True
    assert is_yellow_color({"red": 1.0, "green": 1.0}) is True  # blue defaults to 0
    # Blue color should be False
    assert is_yellow_color({"red": 0.26, "green": 0.52, "blue": 0.96}) is False
    # White / neutral should be False
    assert is_yellow_color({"red": 1.0, "green": 1.0, "blue": 1.0}) is False
    assert is_yellow_color({}) is False


def test_dropdown_exact_match_and_copas(tmp_path):
    """Kasus 1, 2, 3: Sama persis valid, copas sama persis valid, beda kapitalisasi/spasi tidak valid."""
    doc_id = "doc_test_1"
    sheet_title = "Data"
    source = {"title": "data_FO_1", "group": "FO", "id": doc_id, "sheet": sheet_title, "start": 2}
    config = {"sources": [source]}

    mock_api = MockAPI({
        doc_id: {
            "sheets": [{
                "properties": {"sheetId": 12345, "title": sheet_title},
                "data": [
                    {
                        "rowData": [{
                            "values": [
                                {"formattedValue": "NO"},
                                {"formattedValue": "SITE ID"},
                                {"formattedValue": "STATUS VERIFIKASI COD"},
                            ]
                        }]
                    },
                    {
                        "rowData": [{
                            "values": [
                                {},
                                {},
                                {
                                    "dataValidation": {
                                        "condition": {
                                            "type": "ONE_OF_LIST",
                                            "values": [
                                                {"userEnteredValue": "02.OGP SURVEY"},
                                                {"userEnteredValue": "08.RFS"},
                                            ]
                                        }
                                    }
                                }
                            ]
                        }]
                    }
                ]
            }]
        }
    })

    # Rows:
    # Row 1: Header
    # Row 2: "02.OGP SURVEY" -> Exact match -> Valid
    # Row 3: "08.RFS" -> Copied/pasted identical -> Valid
    # Row 4: "08.rfs" -> Lowercase -> Invalid
    # Row 5: "02.OGP SURVEY " -> Extra space -> Invalid
    # Row 6: "00.UNKNOWN" -> Not in list -> Invalid
    mock_api.read_responses[doc_id] = [
        ["NO", "SITE ID", "STATUS VERIFIKASI COD"],
        ["1", "SITE001", "02.OGP SURVEY"],
        ["2", "SITE002", "08.RFS"],
        ["3", "SITE003", "08.rfs"],
        ["4", "SITE004", "02.OGP SURVEY "],
        ["5", "SITE005", "00.UNKNOWN"],
    ]

    report_file = tmp_path / "val-report.json"
    validator = MitraValidator(mock_api, config, report_path=report_file)
    report = validator.run()

    assert report["status"] == "success"
    findings = report["findings"]
    # Only rows 4, 5, 6 should produce dropdown findings
    dropdown_findings = [f for f in findings if f["finding_type"] == "dropdown_mismatch"]
    assert len(dropdown_findings) == 3

    assert dropdown_findings[0]["row"] == 4
    assert dropdown_findings[0]["current_value"] == "08.rfs"
    assert dropdown_findings[1]["row"] == 5
    assert dropdown_findings[1]["current_value"] == "02.OGP SURVEY "
    assert dropdown_findings[2]["row"] == 6
    assert dropdown_findings[2]["current_value"] == "00.UNKNOWN"

    # Verify atomic report file exists and valid JSON
    assert report_file.exists()
    disk_data = json.loads(report_file.read_text())
    assert disk_data["run_id"] == report["run_id"]


def test_dropdown_range_reference(tmp_path):
    """Kasus 4: Dropdown berbasis ONE_OF_RANGE."""
    doc_id = "doc_range"
    sheet_title = "Data"
    source = {"title": "data_RANGE", "group": "FO", "id": doc_id, "sheet": sheet_title, "start": 2}
    config = {"sources": [source]}

    mock_api = MockAPI({
        doc_id: {
            "sheets": [{
                "properties": {"sheetId": 999, "title": sheet_title},
                "data": [
                    {
                        "rowData": [{
                            "values": [
                                {"formattedValue": "SITE ID"},
                                {"formattedValue": "PILIHAN RANGE"},
                            ]
                        }]
                    },
                    {
                        "rowData": [{
                            "values": [
                                {},
                                {
                                    "dataValidation": {
                                        "condition": {
                                            "type": "ONE_OF_RANGE",
                                            "values": [
                                                {"userEnteredValue": "='RefTab'!A2:A5"}
                                            ]
                                        }
                                    }
                                }
                            ]
                        }]
                    }
                ]
            }]
        }
    })

    # Response for 'RefTab'!A2:A5
    mock_api.read_responses[(doc_id, "'RefTab'!A2:A5")] = [
        ["Option Alpha"],
        ["Option Beta"],
        ["Option Gamma"],
    ]

    mock_api.read_responses[doc_id] = [
        ["SITE ID", "PILIHAN RANGE"],
        ["S01", "Option Alpha"],
        ["S02", "Option Delta"],  # Invalid
        ["S03", ""],              # Empty optional -> not dropdown error
    ]

    report_file = tmp_path / "range-report.json"
    validator = MitraValidator(mock_api, config, report_path=report_file)
    report = validator.run()

    assert report["status"] == "success"
    dd_findings = [f for f in report["findings"] if f["finding_type"] == "dropdown_mismatch"]
    assert len(dd_findings) == 1
    assert dd_findings[0]["row"] == 3
    assert dd_findings[0]["current_value"] == "Option Delta"
    assert "Option Alpha" in dd_findings[0]["allowed_options"]


def test_yellow_mandatory_new_fo(tmp_path):
    """Kasus 6 & 7: Mitra FO baru + SITE ID terisi + kolom kuning kosong. SITE ID kosong -> dilewati."""
    doc_id = "doc_fo_new"
    sheet_title = "Data"
    source = {"title": "data_ADYAWINSA", "group": "FO", "id": doc_id, "sheet": sheet_title, "start": 2}
    config = {"sources": [source]}

    mock_api = MockAPI({
        doc_id: {
            "sheets": [{
                "properties": {"sheetId": 777, "title": sheet_title},
                "data": [
                    {
                        "rowData": [{
                            "values": [
                                {"formattedValue": "NO"},
                                {"formattedValue": "SITE ID", "effectiveFormat": {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 0.0}}},
                                {"formattedValue": "SP", "effectiveFormat": {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 0.0}}},
                                {"formattedValue": "WITEL", "effectiveFormat": {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 0.0}}},
                                {"formattedValue": "OPTIONAL COL"}, # White header
                            ]
                        }]
                    },
                    {"rowData": []}
                ]
            }]
        }
    })

    # Rows:
    # Row 2: SITE ID="S01", SP="", WITEL="JAKARTA" -> SP mandatory empty
    # Row 3: SITE ID="   ", SP="", WITEL="" -> SITE ID whitespace only -> NO mandatory findings
    # Row 4: SITE ID="", SP="", WITEL="" -> Empty SITE ID -> NO mandatory findings
    # Row 5: SITE ID="S02", SP="SP1", WITEL="   " -> WITEL whitespace only -> mandatory empty
    mock_api.read_responses[doc_id] = [
        ["NO", "SITE ID", "SP", "WITEL", "OPTIONAL COL"],
        ["1", "S01", "", "JAKARTA", ""],
        ["2", "   ", "", "", ""],
        ["3", "", "", "", ""],
        ["4", "S02", "SP1", "   ", ""],
    ]

    report_file = tmp_path / "fo-report.json"
    validator = MitraValidator(mock_api, config, report_path=report_file)
    report = validator.run()

    mand_findings = [f for f in report["findings"] if f["finding_type"] == "mandatory_empty"]
    assert len(mand_findings) == 2

    # Row 2: SP empty
    assert mand_findings[0]["row"] == 2
    assert mand_findings[0]["col_name"] == "SP"
    assert mand_findings[0]["col_letter"] == "C"
    assert mand_findings[0]["cell_address"] == "C2"
    assert mand_findings[0]["cell_url"] == f"https://docs.google.com/spreadsheets/d/{doc_id}/edit#gid=777&range=C2"

    # Row 5: WITEL whitespace
    assert mand_findings[1]["row"] == 5
    assert mand_findings[1]["col_name"] == "WITEL"
    assert mand_findings[1]["cell_address"] == "D5"


def test_ta_bto_pst_excluded_from_yellow_mandatory(tmp_path):
    """Kasus 8: TA/BTO/PST dikecualikan dari kolom kuning FO baru, tetapi dropdown tetap diperiksa."""
    doc_id = "doc_ta"
    sheet_title = "TIF ORDER"
    source = {"title": "ta", "group": "FO", "id": doc_id, "sheet": sheet_title, "start": 4}
    config = {"sources": [source]}

    mock_api = MockAPI({
        doc_id: {
            "sheets": [{
                "properties": {"sheetId": 333, "title": sheet_title},
                "data": [
                    {
                        "rowData": [{
                            "values": [
                                {"formattedValue": "SITE ID", "effectiveFormat": {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 0.0}}},
                                {"formattedValue": "KOLOM KUNING", "effectiveFormat": {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 0.0}}},
                                {"formattedValue": "STATUS BA PERIZINAN"},
                            ]
                        }]
                    },
                    {
                        "rowData": [{
                            "values": [
                                {},
                                {},
                                {
                                    "dataValidation": {
                                        "condition": {
                                            "type": "ONE_OF_LIST",
                                            "values": [
                                                {"userEnteredValue": "03.Approved TIF"}
                                            ]
                                        }
                                    }
                                }
                            ]
                        }]
                    }
                ]
            }]
        }
    })

    # Row 4: SITE ID="TA01", KOLOM KUNING="" (should NOT trigger mandatory_empty because TA is excluded),
    # STATUS BA PERIZINAN="01.Wrong" (should trigger dropdown_mismatch)
    mock_api.read_responses[doc_id] = [
        ["", "", ""],
        ["", "", ""],
        ["SITE ID", "KOLOM KUNING", "STATUS BA PERIZINAN"],
        ["TA01", "", "01.Wrong"],
    ]

    report_file = tmp_path / "ta-report.json"
    validator = MitraValidator(mock_api, config, report_path=report_file)
    report = validator.run()

    mand_findings = [f for f in report["findings"] if f["finding_type"] == "mandatory_empty"]
    dd_findings = [f for f in report["findings"] if f["finding_type"] == "dropdown_mismatch"]

    assert len(mand_findings) == 0  # TA is excluded from yellow mandatory
    assert len(dd_findings) == 1   # Dropdown is still checked!
    assert dd_findings[0]["row"] == 4
    assert dd_findings[0]["current_value"] == "01.Wrong"


def test_radio_does_not_inherit_yellow_mandatory(tmp_path):
    """Kasus 9: RADIO tidak mewarisi aturan kolom kuning FO."""
    doc_id = "doc_radio"
    sheet_title = "Data"
    source = {"title": "pst_radio", "group": "RADIO", "id": doc_id, "sheet": sheet_title, "start": 2}
    config = {"sources": [source]}

    mock_api = MockAPI({
        doc_id: {
            "sheets": [{
                "properties": {"sheetId": 444, "title": sheet_title},
                "data": [
                    {
                        "rowData": [{
                            "values": [
                                {"formattedValue": "HOP", "effectiveFormat": {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 0.0}}},
                                {"formattedValue": "KOLOM KUNING", "effectiveFormat": {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 0.0}}},
                                {"formattedValue": "Flagging Progress"},
                            ]
                        }]
                    },
                    {
                        "rowData": [{
                            "values": [
                                {},
                                {},
                                {
                                    "dataValidation": {
                                        "condition": {
                                            "type": "ONE_OF_LIST",
                                            "values": [
                                                {"userEnteredValue": "08.RFS"}
                                            ]
                                        }
                                    }
                                }
                            ]
                        }]
                    }
                ]
            }]
        }
    })

    mock_api.read_responses[doc_id] = [
        ["HOP", "KOLOM KUNING", "Flagging Progress"],
        ["HOP-01", "", "08.RFS"],
        ["HOP-02", "", "WRONG_FLAG"],
    ]

    report_file = tmp_path / "radio-report.json"
    validator = MitraValidator(mock_api, config, report_path=report_file)
    report = validator.run()

    mand_findings = [f for f in report["findings"] if f["finding_type"] == "mandatory_empty"]
    dd_findings = [f for f in report["findings"] if f["finding_type"] == "dropdown_mismatch"]

    assert len(mand_findings) == 0  # Radio never has yellow mandatory
    assert len(dd_findings) == 1   # Dropdown mismatch caught
    assert dd_findings[0]["identifier"] == "HOP-02"


def test_no_double_finding_when_yellow_has_dropdown_and_empty(tmp_path):
    """Bila kolom kuning wajib juga memiliki dropdown dan kosong, hanya buat temuan mandatory_empty."""
    doc_id = "doc_fo_combo"
    sheet_title = "Data"
    source = {"title": "data_CSA", "group": "FO", "id": doc_id, "sheet": sheet_title, "start": 2}
    config = {"sources": [source]}

    mock_api = MockAPI({
        doc_id: {
            "sheets": [{
                "properties": {"sheetId": 555, "title": sheet_title},
                "data": [
                    {
                        "rowData": [{
                            "values": [
                                {"formattedValue": "SITE ID"},
                                {
                                    "formattedValue": "STATUS VERIFIKASI COD",
                                    "effectiveFormat": {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 0.0}}
                                },
                            ]
                        }]
                    },
                    {
                        "rowData": [{
                            "values": [
                                {},
                                {
                                    "dataValidation": {
                                        "condition": {
                                            "type": "ONE_OF_LIST",
                                            "values": [{"userEnteredValue": "08.RFS"}]
                                        }
                                    }
                                }
                            ]
                        }]
                    }
                ]
            }]
        }
    })

    mock_api.read_responses[doc_id] = [
        ["SITE ID", "STATUS VERIFIKASI COD"],
        ["S01", ""],  # Empty cell on yellow + dropdown column
    ]

    validator = MitraValidator(mock_api, config, report_path=tmp_path / "rep.json")
    report = validator.run()

    findings = report["findings"]
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "mandatory_empty"
    assert findings[0]["col_name"] == "STATUS VERIFIKASI COD"


def test_reviewer_reproduction_raw_rows_and_row_numbers(tmp_path):
    """Reproduksi Reviewer: Baris 3 tanpa SITE ID memiliki dropdown terisi salah.
    
    Data:
    Baris 1: SITE ID | STATUS
    Baris 2: SITE-A  | WRONG-A
    Baris 3: kosong  | WRONG-NO-ID
    Baris 4: SITE-B  | WRONG-B
    
    Pemeriksaan data mentah harus menghasilkan temuan B2, B3, B4.
    Jalur otomatis impor dan CLI harus menghasilkan temuan dan lokasi yang identik!
    """
    from import_mitra.mapping import SourceData

    doc_id = "doc_repro"
    sheet_title = "Data"
    source = {"title": "data_REPRO", "group": "FO", "id": doc_id, "sheet": sheet_title, "start": 2}
    config = {"sources": [source], "aliases": {"SITEID": "SITE ID", "STATUS": "STATUS"}}

    mock_api = MockAPI({
        doc_id: {
            "sheets": [{
                "properties": {"sheetId": 111, "title": sheet_title},
                "data": [
                    {
                        "rowData": [{
                            "values": [
                                {"formattedValue": "SITE ID"},
                                {"formattedValue": "STATUS"},
                            ]
                        }]
                    },
                    {
                        "rowData": [
                            {"values": []},
                            {"values": [{}, {"dataValidation": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "VALID"}]}}}]},
                            {"values": [{}, {"dataValidation": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "VALID"}]}}}]},
                            {"values": [{}, {"dataValidation": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "VALID"}]}}}]},
                        ]
                    }
                ]
            }]
        }
    })

    raw_values = [
        ["SITE ID", "STATUS"],
        ["SITE-A", "WRONG-A"],
        ["", "WRONG-NO-ID"],
        ["SITE-B", "WRONG-B"],
    ]
    mock_api.read_responses[doc_id] = raw_values

    # 1. Jalur CLI validate (tanpa cached_datasets)
    rep_cli = tmp_path / "rep-cli.json"
    val_cli = MitraValidator(mock_api, config, report_path=rep_cli)
    report_cli = val_cli.run()

    findings_cli = report_cli["findings"]
    assert len(findings_cli) == 3
    assert [f["cell_address"] for f in findings_cli] == ["B2", "B3", "B4"]
    assert [f["row"] for f in findings_cli] == [2, 3, 4]
    assert findings_cli[0]["current_value"] == "WRONG-A"
    assert findings_cli[1]["current_value"] == "WRONG-NO-ID"
    assert findings_cli[1]["identifier"] == "Baris 3"  # Baris tanpa SITE ID tetap valid terperiksa
    assert findings_cli[2]["current_value"] == "WRONG-B"

    # 2. Jalur otomatis impor (dengan cached_datasets yang membawa raw_values)
    # Simulator SourceData dari parse_source: rows tersaring (baris 3 hilang), tapi raw_values utuh!
    filtered_rows = [["SITE-A", "WRONG-A"], ["SITE-B", "WRONG-B"]]
    cached_ds = [
        SourceData(
            config=source,
            headers=["SITE ID", "STATUS"],
            keys=["SITEID", "STATUS"],
            rows=filtered_rows,
            key_index=0,
            raw_values=raw_values,
            header_row=0,
        )
    ]

    rep_auto = tmp_path / "rep-auto.json"
    val_auto = MitraValidator(mock_api, config, report_path=rep_auto)
    report_auto = val_auto.run(cached_datasets=cached_ds)

    findings_auto = report_auto["findings"]
    assert len(findings_auto) == 3
    assert [f["cell_address"] for f in findings_auto] == ["B2", "B3", "B4"]
    assert [f["row"] for f in findings_auto] == [2, 3, 4]
    assert findings_auto == findings_cli  # Identik 100%!


def test_header_row_detection_non_first_row(tmp_path):
    """Header berada pada baris 4 (seperti pada sumber TA). Header dan nomor baris harus tepat."""
    doc_id = "doc_offset_header"
    sheet_title = "Data"
    source = {"title": "ta", "group": "FO", "id": doc_id, "sheet": sheet_title, "start": 5}
    config = {"sources": [source], "aliases": {"SITEID": "SITE ID", "STATUS": "STATUS"}}

    # Row 1-3: Blank / Title Banner
    # Row 4: Header
    mock_api = MockAPI({
        doc_id: {
            "sheets": [{
                "properties": {"sheetId": 222, "title": sheet_title},
                "data": [
                    {
                        "rowData": [
                            {"values": [{"formattedValue": "TITLE 1"}]},
                            {"values": [{"formattedValue": "TITLE 2"}]},
                            {"values": [{"formattedValue": "TITLE 3"}]},
                            {"values": [
                                {"formattedValue": "SITE ID"},
                                {"formattedValue": "STATUS"},
                            ]},
                        ]
                    },
                    {
                        "rowData": [
                            {}, {}, {}, {},
                            {"values": [{}, {"dataValidation": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "VALID"}]}}}]},
                        ]
                    }
                ]
            }]
        }
    })

    raw_values = [
        ["TITLE 1"],
        ["TITLE 2"],
        ["TITLE 3"],
        ["SITE ID", "STATUS"],  # Row 4
        ["TA-01", "INVALID_VAL"],  # Row 5
    ]
    mock_api.read_responses[doc_id] = raw_values

    validator = MitraValidator(mock_api, config, report_path=tmp_path / "rep-header.json")
    report = validator.run()

    assert report["status"] == "success"
    findings = report["findings"]
    assert len(findings) == 1
    assert findings[0]["row"] == 5
    assert findings[0]["cell_address"] == "B5"
    assert findings[0]["current_value"] == "INVALID_VAL"


def test_missing_identity_column_records_unverified_scope(tmp_path):
    """Bila kolom identitas tidak ditemukan, jangan tebak kolom 0. Catat unverified_scopes dan status partial."""
    doc_id = "doc_no_id"
    sheet_title = "Data"
    source = {"title": "data_UNKNOWN", "group": "FO", "id": doc_id, "sheet": sheet_title, "start": 2}
    config = {"sources": [source]}

    mock_api = MockAPI({
        doc_id: {
            "sheets": [{
                "properties": {"sheetId": 333, "title": sheet_title},
                "data": [
                    {
                        "rowData": [{
                            "values": [
                                {"formattedValue": "CATATAN"},
                                {"formattedValue": "KETERANGAN"},
                            ]
                        }]
                    },
                    {"rowData": []}
                ]
            }]
        }
    })
    mock_api.read_responses[doc_id] = [
        ["CATATAN", "KETERANGAN"],
        ["Catatan A", "Ket B"],
    ]

    validator = MitraValidator(mock_api, config, report_path=tmp_path / "rep-no-id.json")
    report = validator.run()

    # Status harus partial, bukan success!
    assert report["status"] == "partial"
    assert len(report["unverified_scopes"]) == 1
    assert "tidak ditemukan" in report["unverified_scopes"][0]["reason"]
    assert len(report["findings"]) == 0


def test_checkbox_boolean_validation_is_ignored(tmp_path):
    """Checkbox dengan tipe BOOLEAN tidak boleh dianggap sebagai dropdown mismatch atau error."""
    doc_id = "doc_checkbox"
    sheet_title = "Data"
    source = {"title": "data_CB", "group": "FO", "id": doc_id, "sheet": sheet_title, "start": 2}
    config = {"sources": [source], "aliases": {"SITEID": "SITE ID"}}

    mock_api = MockAPI({
        doc_id: {
            "sheets": [{
                "properties": {"sheetId": 444, "title": sheet_title},
                "data": [
                    {
                        "rowData": [{
                            "values": [
                                {"formattedValue": "SITE ID"},
                                {"formattedValue": "IS_ACTIVE"},
                            ]
                        }]
                    },
                    {
                        "rowData": [
                            {"values": []},
                            {"values": [{}, {"dataValidation": {"condition": {"type": "BOOLEAN"}}}]},
                        ]
                    }
                ]
            }]
        }
    })
    mock_api.read_responses[doc_id] = [
        ["SITE ID", "IS_ACTIVE"],
        ["S01", "TRUE"],
        ["S02", "FALSE"],
    ]

    validator = MitraValidator(mock_api, config, report_path=tmp_path / "rep-cb.json")
    report = validator.run()

    assert report["status"] == "success"
    assert len(report["findings"]) == 0
    assert len(report["unverified_scopes"]) == 0


def test_atomic_publish_overwrite_protection(tmp_path):
    """Run yang lebih lama/lambat tidak boleh menimpa file laporan yang sudah diperbarui oleh run lebih baru."""
    from datetime import datetime, timedelta, timezone

    rep_path = tmp_path / "validation-report.json"
    now = datetime.now(timezone.utc)
    future = now + timedelta(minutes=10)

    # Buat laporan di disk yang memiliki finished_at di masa depan / lebih baru
    newer_report = {
        "run_id": "newer-run",
        "finished_at": future.isoformat(),
        "status": "success",
        "findings": [],
    }
    rep_path.write_text(json.dumps(newer_report))

    mock_api = MockAPI()
    validator = MitraValidator(mock_api, {"sources": []}, report_path=rep_path)
    
    # Run saat ini dengan finished_at = now (lebih tua dari existing)
    older_report = {
        "run_id": "older-run",
        "finished_at": now.isoformat(),
        "status": "success",
        "findings": [{"test": 1}],
    }
    validator._publish_atomic(older_report)

    # Verifikasi isi disk TIDAK berubah (tetap newer-run)
    current_disk = json.loads(rep_path.read_text())
    assert current_disk["run_id"] == "newer-run"


def test_exclusive_validation_locking(tmp_path):
    """Uji cross-process lock: exclusive_validation mencegah konkurensi validator."""
    from import_mitra.jobs import BusyError, exclusive_validation

    lock_dir = tmp_path / "state"

    with exclusive_validation(lock_dir):
        # Saat lock dipegang, proses lain harus gagal dengan BusyError
        with pytest.raises(BusyError) as exc_info, exclusive_validation(lock_dir):
            pass
        assert "sedang berjalan" in str(exc_info.value)



def test_large_schema_is_batched_without_losing_original_row(tmp_path):
    source = {"title": "data_BATCH", "group": "FO", "id": "fake", "sheet": "Data", "start": 2}
    raw = [["SITE ID", "STATUS"]] + [[f"S{i}", "OK"] for i in range(500)]
    raw[500][1] = "WRONG"

    class BatchAPI:
        def __init__(self):
            self.ranges = []

        def read(self, *_):
            return raw

        def request(self, _method, _doc, *, params):
            self.ranges.extend(params["ranges"])
            rule = {"dataValidation": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "OK"}]}}}
            if len(params["ranges"]) == 2:
                header = {"rowData": [{"values": [{"formattedValue": "SITE ID"}, {"formattedValue": "STATUS"}]}]}
                rows = [{"values": []}, {"values": [{}, rule]}]
                return {"sheets": [{"properties": {"sheetId": 1}, "data": [header, {"rowData": rows}]}]}
            return {"sheets": [{"data": [{"startRow": 500, "rowData": [{"values": [{}, rule]}]}]}]}

    api = BatchAPI()
    result = MitraValidator(api, {"sources": [source]}, tmp_path / "report.json").run()
    assert [f["cell_address"] for f in result["findings"]] == ["B501"]
    assert "'Data'!A501:B501" in api.ranges
    assert all("!" in r for r in api.ranges)
