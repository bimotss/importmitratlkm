"""Replace JPP values from ALL DATA, preserving the existing sheet schema."""
import gzip
import json
import math
import os
import secrets
from datetime import date, datetime, time, timezone
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.formula.translate import Translator
from openpyxl.utils.datetime import to_excel

from .google_api import SHEET_CELL_LIMIT, GoogleSheets
from .importer import value_chunks
from .mapping import col, norm, quoted

MAX_UPLOAD = 25 * 1024 * 1024
MAX_ROWS = 50_000


def read_upload(path, profile):
    """Read saved Excel values only; never execute uploaded formulas or macros."""
    if Path(path).stat().st_size > MAX_UPLOAD:
        raise ValueError("File maksimal 25 MB.")
    try:
        with ZipFile(path) as archive:
            if len(archive.infolist()) > 4096 or sum(i.file_size for i in archive.infolist()) > 256 * 1024 * 1024:
                raise ValueError("Isi Excel terlalu besar setelah dibuka.")
        book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    except (BadZipFile, KeyError, OSError) as exc:
        raise ValueError("File XLSX tidak valid.") from exc
    try:
        if profile["source_sheet"] not in book.sheetnames:
            raise ValueError("File harus mempunyai tab ALL DATA.")
        sheet = book[profile["source_sheet"]]
        if not sheet.max_row or sheet.max_row > MAX_ROWS + 1 or sheet.max_column > 200:
            raise ValueError("Tab ALL DATA maksimal 50.000 baris data dan 200 kolom.")
        iterator = sheet.iter_rows(values_only=True)
        headers = [norm(v) if v is not None else "" for v in next(iterator)]
        names = [h for h in headers if h]
        if len(names) != len(set(names)):
            raise ValueError("Header ALL DATA duplikat; perbaiki file sebelum upload.")
        if not {norm(h) for h in ("Site ID/NE", "NIM Order", "Src Data", "Plan Transport")} <= set(names):
            raise ValueError("Header ALL DATA tidak sesuai format JPP.")
        aliases = profile["aliases"]
        mapping = [headers.index(norm(aliases.get(h, h))) if norm(aliases.get(h, h)) in headers else None
                   for h in profile["headers"]]
        missing = [h for h, index in zip(profile["headers"], mapping)
                   if index is None and h not in profile["formula_columns"]]
        rows = []
        site_index = headers.index(norm("Site ID/NE"))
        for number, source in enumerate(iterator, 2):
            if number > MAX_ROWS + 1:
                raise ValueError("ALL DATA melebihi 50.000 baris data.")
            if not any(v is not None for v in source):
                continue
            if not source[site_index] or str(source[site_index]).startswith("#"):
                raise ValueError(f"Site ID/NE kosong atau error di baris {number}.")
            row = []
            for index in mapping:
                value = source[index] if index is not None and index < len(source) else None
                if isinstance(value, (datetime, date, time)):
                    value = to_excel(value)
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(f"Nilai angka tidak valid di baris {number}.")
                row.append("" if value is None else value)
            rows.append(row)
        if not rows:
            raise ValueError("ALL DATA kosong; data JPP lama tidak diganti.")
        return rows, missing
    finally:
        book.close()


def write_status(folder, **state):
    temporary = folder / f"status-{secrets.token_hex(6)}.tmp"
    temporary.write_text(json.dumps({**state, "updated_at": datetime.now(timezone.utc).isoformat()}))
    temporary.chmod(0o660)
    temporary.replace(folder / "status.json")


class JPPImport:
    def __init__(self, settings, path, api=None):
        self.settings, self.path = settings, Path(path)
        self.profile = json.loads(settings.jpp_profile.read_text())
        self.target = json.loads(settings.cod_profile.read_text())["spreadsheet_id"]
        self.api = api or GoogleSheets(settings.credentials_file, settings.requests_per_minute)

    def inspect_target(self):
        meta = self.api.metadata(self.target)
        matches = [s for s in meta["sheets"] if s["properties"]["sheetId"] == self.profile["sheet_id"]]
        if len(matches) != 1 or matches[0]["properties"]["title"] != self.profile["title"]:
            raise ValueError("Sheet JPP tujuan berubah; pembaruan dibatalkan.")
        sheet = matches[0]
        width = len(self.profile["headers"])
        if sheet["properties"]["gridProperties"]["columnCount"] != width:
            raise ValueError("Jumlah kolom JPP berubah; pembaruan dibatalkan.")
        cells = self.api.cells(self.target, f"{quoted(self.profile['title'])}!A1:{col(width)}2")
        header = [c.get("userEnteredValue", {}).get("stringValue", "") for c in cells[0].get("values", [])]
        if header != self.profile["headers"]:
            raise ValueError("Header JPP berbeda dari format terverifikasi; tidak ada kolom yang diubah.")
        templates = cells[1].get("values", []) if len(cells) > 1 else []
        formulas = {}
        for index, name in enumerate(self.profile["headers"]):
            entered = templates[index].get("userEnteredValue", {}) if index < len(templates) else {}
            formula = entered.get("formulaValue")
            if name in self.profile["formula_columns"]:
                if not formula:
                    raise ValueError(f"Rumus bantu {name} baris 2 tidak ditemukan.")
                formulas[index] = formula
            elif formula:
                raise ValueError(f"Kolom data {name} berisi rumus; periksa sebelum mengganti data.")
        return meta, sheet, formulas

    def run(self, progress=lambda **kw: None, *, dry_run=False):
        progress(phase="jpp_reading", message="Membaca tab ALL DATA dari Excel", total_sources=1, total_tabs=1)
        rows, missing = read_upload(self.path, self.profile)
        meta, sheet, formulas = self.inspect_target()
        width, height = len(self.profile["headers"]), len(rows) + 1
        old_height = sheet["properties"]["gridProperties"]["rowCount"]
        summary = {"total": len(rows), "sources": 1, "tabs": 1, "missing_columns": missing,
                   "columns": width, "source_sheet": self.profile["source_sheet"]}
        if dry_run:
            return {**summary, "dry_run": True}
        allocated = sum(s["properties"]["gridProperties"]["rowCount"] *
                        s["properties"]["gridProperties"]["columnCount"] for s in meta["sheets"])
        if allocated + (height + max(0, height - old_height)) * width > SHEET_CELL_LIMIT:
            raise ValueError("Ruang spreadsheet tidak cukup untuk staging JPP.")
        progress(phase="jpp_backup", message="Menyimpan backup data JPP sebelum pembaruan")
        folder = self.settings.state_dir / "jpp-backups"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = folder / f"jpp-{stamp}-{secrets.token_hex(4)}.jsonl.gz"
        partial = backup.with_suffix(".gz.partial")
        with os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as raw, \
                gzip.GzipFile(fileobj=raw, mode="wb") as stream:
                stream.write((json.dumps({"sheet": sheet, "spreadsheet_id": self.target}) + "\n").encode())
                for first in range(1, old_height + 1, 400):
                    data = self.api.cells(self.target, f"{quoted(self.profile['title'])}!A{first}:{col(width)}{min(first+399, old_height)}")
                    stream.write((json.dumps({"start_row": first, "rows": data}) + "\n").encode())
        partial.replace(backup)
        stage_id = secrets.randbelow(2_000_000_000) + 1
        while stage_id in {s["properties"]["sheetId"] for s in meta["sheets"]}:
            stage_id = secrets.randbelow(2_000_000_000) + 1
        stage_title = f"_IMJPP_{secrets.token_hex(6)}"
        created = False
        try:
            self.api.batch(self.target, [{"addSheet": {"properties": {"sheetId": stage_id,
                "title": stage_title, "hidden": True, "gridProperties": {"rowCount": height, "columnCount": width}}}}],
                idempotent=False)
            created = True
            progress(phase="jpp_staging", message=f"Menyiapkan {len(rows):,} baris JPP", completed_sources=1)
            for first, chunk in value_chunks([self.profile["headers"]] + rows):
                self.api.write(self.target, f"{quoted(stage_title)}!A{first}", chunk)
            staged = self.api.read(self.target, f"{quoted(stage_title)}!A1:{col(width)}{height}")
            expected = [self.profile["headers"]] + rows
            if len(staged) != height or any(list(a) + [""] * (width - len(a)) != b for a, b in zip(staged, expected)):
                raise ValueError("Verifikasi staging JPP gagal; data lama tidak diganti.")
            del staged, expected
            _, current, current_formulas = self.inspect_target()
            if formulas != current_formulas or old_height != current["properties"]["gridProperties"]["rowCount"]:
                raise ValueError("Struktur atau rumus JPP berubah saat persiapan; pembaruan dibatalkan.")
            sid = self.profile["sheet_id"]
            requests = []
            basic_filter = current.get("basicFilter")
            if basic_filter:
                requests.append({"clearBasicFilter": {"sheetId": sid}})
            if height > old_height:
                requests.append({"appendDimension": {"sheetId": sid, "dimension": "ROWS", "length": height-old_height}})
                requests.append({"copyPaste": {"source": {"sheetId": sid, "startRowIndex": 1,
                    "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": width},
                    "destination": {"sheetId": sid, "startRowIndex": old_height, "endRowIndex": height,
                    "startColumnIndex": 0, "endColumnIndex": width}, "pasteType": "PASTE_FORMAT"}})
            area = {"sheetId": sid, "startRowIndex": 1, "endRowIndex": max(height, old_height),
                    "startColumnIndex": 0, "endColumnIndex": width}
            requests.extend([
                {"updateCells": {"range": area, "fields": "userEnteredValue"}},
                {"copyPaste": {"source": {"sheetId": stage_id, "startRowIndex": 1, "endRowIndex": height,
                    "startColumnIndex": 0, "endColumnIndex": width}, "destination": {**area, "endRowIndex": height},
                    "pasteType": "PASTE_VALUES"}},
            ])
            for index, formula in formulas.items():
                origin = f"{col(index+1)}2"
                requests.append({"updateCells": {"range": {"sheetId": sid, "startRowIndex": 1,
                    "endRowIndex": height, "startColumnIndex": index, "endColumnIndex": index+1},
                    "rows": [{"values": [{"userEnteredValue": {"formulaValue":
                        Translator(formula, origin=origin).translate_formula(f"{col(index+1)}{number}")}}]}
                        for number in range(2, height+1)], "fields": "userEnteredValue"}})
            if basic_filter:
                requests.append({"setBasicFilter": {"filter": {**basic_filter,
                    "range": {**basic_filter["range"], "endRowIndex": height}}}})
            progress(phase="jpp_publishing", message="Mengganti data JPP; header dan posisi kolom tetap")
            # Structural growth and replacement must never be replayed after a timeout.
            self.api.batch(self.target, requests, idempotent=False)
            progress(phase="jpp_verifying", message="Memeriksa seluruh nilai hasil update JPP")
            self.inspect_target()
            actual = self.api.read(self.target, f"{quoted(self.profile['title'])}!A2:{col(width)}{max(height, old_height)}")
            for index, row in enumerate(actual):
                padded = row + [""] * (width-len(row))
                target = rows[index] if index < len(rows) else [""] * width
                if any(padded[c] != target[c] for c in range(width) if c not in formulas):
                    raise ValueError("Verifikasi nilai JPP gagal; backup tersedia untuk pemeriksaan.")
            if len(actual) < len(rows):
                raise ValueError("Jumlah baris hasil JPP tidak sesuai; periksa backup.")
            for index, formula in formulas.items():
                address = f"{col(index+1)}{height}"
                last = self.api.cells(self.target, f"{quoted(self.profile['title'])}!{address}:{address}")
                expected_formula = Translator(formula, origin=f"{col(index+1)}2").translate_formula(address)
                if not last or last[0].get("values", [{}])[0].get("userEnteredValue", {}).get("formulaValue") != expected_formula:
                    raise ValueError("Rumus bantu JPP tidak sesuai setelah update; periksa backup.")
            return {**summary, "backup": str(backup)}
        finally:
            if created:
                # Cleanup cannot hide a successful publish or the original failure.
                try:
                    self.api.batch(self.target, [{"deleteSheet": {"sheetId": stage_id}}], idempotent=False)
                except Exception:  # noqa: BLE001 -- cleanup must not hide the publish result.
                    import logging
                    logging.getLogger(__name__).warning("Staging JPP perlu dibersihkan: %s", stage_title)
