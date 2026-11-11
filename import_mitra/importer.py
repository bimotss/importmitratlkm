import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .mapping import canonical, col, norm, parse_source, partner_formula, project, quoted

LOG = logging.getLogger(__name__)
STAGING_PATTERN = re.compile(r"^_IM_STAGING_[a-f0-9]{12}_\d+$")


@dataclass
class Table:
    title: str
    headers: list
    rows: list
    formula: str | None = None

    @property
    def height(self):
        return max(2, len(self.rows) + 1)


def value_chunks(rows, max_bytes=1_500_000, max_cells=40_000):
    """Bound payload by bytes as well as cells: update notes can be very long."""
    batch, size, cells, start = [], 100, 0, 1
    for row in rows:
        row_size = len(json.dumps(row, ensure_ascii=False).encode()) + 2
        if row_size > max_bytes:
            raise ValueError("Satu baris melebihi batas payload aman Google API.")
        if batch and (size + row_size > max_bytes or cells + len(row) > max_cells):
            yield start, batch
            start += len(batch)
            batch, size, cells = [], 100, 0
        batch.append(row)
        size += row_size
        cells += len(row)
    if batch:
        yield start, batch


def build_tables(datasets, config, interval_seconds=3600, public_url=""):
    aliases, schemas = config["aliases"], config["schemas"]
    fo, radio, combined, tables, audit = [], [], [], [], []
    now = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%d/%m/%Y %H:%M:%S")
    for data in datasets:
        group = data.config["group"].lower()
        group_headers = schemas[group]
        normalized = project(data, group_headers, aliases)
        (fo if group == "fo" else radio).extend(normalized)
        combined.extend(project(data, schemas["all"], aliases))
        tables.append(Table(data.config["title"], ["MITRA"] + data.headers,
                            [[""] + row for row in data.rows], partner_formula(data)))
        target_keys = [norm(canonical(h, data.config["group"], aliases)) for h in group_headers]
        for i, (h, key) in enumerate(zip(data.headers, data.keys)):
            index = target_keys.index(norm(key)) if norm(key) in target_keys else -1
            audit.append([data.config["title"], data.config["group"], col(i + 1), h,
                          group_headers[index] if index >= 0 else "Tab mitra saja",
                          col(index + 1) if index >= 0 else ""])
    if len(fo) + len(radio) != len(combined):
        raise ValueError("Rekonsiliasi jumlah baris gagal.")
    tables.extend([
        Table("DATA TIF FO", schemas["fo"], fo),
        Table("DATA TIF RADIO", schemas["radio"], radio),
        Table("DataDisatukan", schemas["all"], combined),
        Table("_Import_Status", ["MITRA", "JENIS", "STATUS", "BARIS", "DIPERBARUI", "KETERANGAN"],
              [[d.config["title"], d.config["group"], "Selesai", len(d.rows), now, "VPS Python"]
               for d in datasets]),
        Table("_Peta_Kolom", ["MITRA", "JENIS", "KOLOM SUMBER", "JUDUL SUMBER",
                             "JUDUL GABUNGAN", "KOLOM GABUNGAN"], audit),
        Table("Kontrol", ["KONTROL VPS", "NILAI"], [
            ["STATUS", f"Selesai: {len(combined):,} baris"], ["MESIN", "Python di VPS (tanpa Apps Script)"],
            ["AUTO UPDATE", f"Setiap {interval_seconds // 60} menit selama service VPS hidup"],
            ["TERAKHIR SELESAI", now], ["REFRESH MANUAL", public_url or "Gunakan dashboard VPS"],
            ["TOMBOL", "Buka dashboard → Refresh sekarang"],
            ["CATATAN", "Nonaktifkan trigger dan menu impor Apps Script lama saat pindah ke VPS."],
        ]),
    ])
    return tables, {"fo": len(fo), "radio": len(radio), "total": len(combined)}


def formatting(sheet_id, table):
    width, height = len(table.headers), table.height
    full = {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": height,
            "startColumnIndex": 0, "endColumnIndex": width}
    requests = [
        {"repeatCell": {"range": full, "cell": {"userEnteredFormat": {
            "backgroundColor": {"red": 1, "green": 1, "blue": 1}, "wrapStrategy": "CLIP",
            "verticalAlignment": "MIDDLE",
            "textFormat": {"fontFamily": "Arial", "fontSize": 10, "bold": False}}},
            "fields": "userEnteredFormat"}},
        {"repeatCell": {"range": {**full, "endRowIndex": 1}, "cell": {"userEnteredFormat": {
            "backgroundColor": {"red": .91, "green": .92, "blue": .93}, "wrapStrategy": "WRAP",
            "textFormat": {"fontFamily": "Arial", "fontSize": 10, "bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,wrapStrategy,textFormat)"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
            "startIndex": 0, "endIndex": height}, "properties": {"pixelSize": 24}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
            "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 58}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS",
            "startIndex": 0, "endIndex": width}, "properties": {"pixelSize": 145}, "fields": "pixelSize"}},
    ]
    for i, header in enumerate(table.headers):
        n = norm(header)
        pattern = None
        kind = "NUMBER"
        if re.search(r"\bDATE\b|\bINHAND\b|\bIN-HAND\b", header.upper()) or n.startswith(("TGL", "TANGGAL")) or n in {"PLANRFS", "PLANNEDRFSDATE"}:
            kind, pattern = "DATE", "dd/MM/yyyy"
        elif n.startswith("NILAI"):
            pattern = "#,##0.##"
        elif "DEVIASI" in n:
            kind, pattern = "PERCENT", "0.0%"
        if pattern:
            requests.append({"repeatCell": {"range": {**full, "startRowIndex": 1,
                "startColumnIndex": i, "endColumnIndex": i + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": kind, "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat"}})
        if re.search("KETERANGAN|REMARK", header.upper()):
            requests.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id,
                "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": 380}, "fields": "pixelSize"}})
    if table.title == "Kontrol":
        requests.extend([
            {"setDataValidation": {"range": full}},  # hapus checkbox Apps Script lama
            {"repeatCell": {"range": full, "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                            "fields": "userEnteredFormat.wrapStrategy"}},
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                "startIndex": 1, "endIndex": 2}, "properties": {"pixelSize": 500}, "fields": "pixelSize"}},
        ])
    elif table.rows:
        requests.append({"setBasicFilter": {"filter": {"range": full}}})
    return requests


class Importer:
    def __init__(self, api, config, *, interval_seconds=3600, public_url=""):
        self.api, self.config = api, config
        self.target = config["target_spreadsheet_id"]
        self.interval_seconds, self.public_url = interval_seconds, public_url
        self.cod_sync = None

    def collect(self, progress):
        datasets = []
        sources = self.config["sources"]
        for i, source in enumerate(sources):
            LOG.info("[%s/%s] Membaca %s...", i + 1, len(sources), source["title"])
            progress(phase="reading", message=f"Membaca {source['title']}", source=source["title"],
                     completed_sources=i, total_sources=len(sources))
            # Entire tab: new rows are included automatically; values API trims unused cells.
            values = self.api.read(source["id"], quoted(source["sheet"]))
            data = parse_source(values, source, self.config["aliases"])
            partner_formula(data)  # Validasi HOP sebelum ada tulisan ke file tujuan.
            datasets.append(data)
            LOG.info("[%s/%s] %s OK: %s baris", i + 1, len(sources), source["title"], len(data.rows))
        progress(completed_sources=len(sources))
        return datasets

    def run(self, progress=lambda **kwargs: None, *, dry_run=False):
        datasets = self.collect(progress)
        tables, counts = build_tables(datasets, self.config, self.interval_seconds, self.public_url)
        if dry_run:
            result = {**counts, "dry_run": True, "sources": len(datasets), "tabs": len(tables)}
            if self.cod_sync:
                result["cod"] = self.cod_sync.run(datasets, progress, dry_run=True)
            return result
        self.publish(tables, progress)
        result = {**counts, "sources": len(datasets), "tabs": len(tables)}
        if self.cod_sync:
            result["cod"] = self.cod_sync.run(datasets, progress)
        return result

    def publish(self, tables, progress):
        meta = self.api.metadata(self.target)
        properties = [s["properties"] for s in meta["sheets"]]
        stale = [p for p in properties if STAGING_PATTERN.fullmatch(p["title"])]
        if stale:
            self.api.batch(self.target, [{"deleteSheet": {"sheetId": p["sheetId"]}} for p in stale],
                           idempotent=False)
            properties = [p for p in properties if p not in stale]
        grid_cells = sum(p["gridProperties"]["rowCount"] * p["gridProperties"]["columnCount"]
                         for p in properties)
        stage_cells = sum(t.height * len(t.headers) for t in tables)
        # Include space for any output tabs not created yet.
        names = {p["title"]: p for p in properties}
        missing = [t for t in tables if t.title not in names]
        if grid_cells + stage_cells + sum(t.height * len(t.headers) for t in missing) > 10_000_000:
            raise ValueError("Ruang Google Sheet tidak cukup untuk staging aman (batas 10 juta sel).")
        used = {p["sheetId"] for p in properties}

        def new_id():
            while (value := secrets.randbelow(2_000_000_000) + 1) in used:
                pass
            used.add(value)
            return value

        create_dest = []
        for table in missing:
            p = {"sheetId": new_id(), "title": table.title,
                 "gridProperties": {"rowCount": table.height, "columnCount": len(table.headers)}}
            names[table.title] = p
            create_dest.append({"addSheet": {"properties": p}})
        if create_dest:
            self.api.batch(self.target, create_dest, idempotent=False)
        run_id = secrets.token_hex(6)
        stages = [{"sheetId": new_id(), "title": f"_IM_STAGING_{run_id}_{i}", "hidden": True,
                   "gridProperties": {"rowCount": t.height, "columnCount": len(t.headers)}}
                  for i, t in enumerate(tables)]
        committed = False
        try:
            self.api.batch(self.target, [{"addSheet": {"properties": p}} for p in stages], idempotent=False)
            for i, (table, stage) in enumerate(zip(tables, stages)):
                progress(phase="staging", message=f"Menyiapkan {table.title}", completed_tabs=i,
                         total_tabs=len(tables))
                for start, chunk in value_chunks([table.headers] + table.rows):
                    self.api.write(self.target, f"{quoted(stage['title'])}!A{start}", chunk)
            styles = [r for table, stage in zip(tables, stages)
                      for r in formatting(stage["sheetId"], table) if "repeatCell" in r]
            for start in range(0, len(styles), 80):
                self.api.batch(self.target, styles[start:start + 80])
            progress(phase="validating", message="Memeriksa hasil staging", completed_tabs=len(tables))
            # Read back the staged headers before touching existing output cells.
            ranges = [f"{quoted(s['title'])}!A1:{col(len(t.headers))}1" for s, t in zip(stages, tables)]
            actual = self.api.read_many(self.target, ranges)
            if len(actual) != len(tables) or any(r.get("values", [[]])[0] != t.headers
                                                  for r, t in zip(actual, tables)):
                raise ValueError("Verifikasi header staging gagal; hasil lama tidak diganti.")
            requests = []
            for table, stage in zip(tables, stages):
                sheet_id = names[table.title]["sheetId"]
                grid = {"rowCount": table.height, "columnCount": len(table.headers), "frozenRowCount": 1}
                full = {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": table.height,
                        "startColumnIndex": 0, "endColumnIndex": len(table.headers)}
                requests.extend([
                    {"clearBasicFilter": {"sheetId": sheet_id}},
                    {"updateCells": {"range": {"sheetId": sheet_id}, "fields": "userEnteredValue"}},
                    {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": grid},
                        "fields": "gridProperties(rowCount,columnCount,frozenRowCount)"}},
                    {"copyPaste": {"source": {**full, "sheetId": stage["sheetId"]}, "destination": full,
                                   "pasteType": "PASTE_NORMAL", "pasteOrientation": "NORMAL"}},
                ])
                if table.formula:
                    requests.append({"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": 1,
                        "columnIndex": 0}, "rows": [{"values": [{"userEnteredValue": {
                            "formulaValue": table.formula}}]}], "fields": "userEnteredValue"}})
                requests.extend(r for r in formatting(sheet_id, table) if "repeatCell" not in r)
            progress(phase="publishing", message="Mengganti hasil sekaligus setelah semua sumber siap")
            # No deletes here: repeating this batch after a network timeout is safe.
            # Google validates/applies the whole batch atomically, including all 3 summary tabs.
            self.api.batch(self.target, requests)
            committed = True
        finally:
            # Cleanup is separate from commit; a cleanup error must not report a successful import as failed.
            try:
                live = self.api.metadata(self.target)
                ids = {s["properties"]["sheetId"] for s in live["sheets"]}
                cleanup = [{"deleteSheet": {"sheetId": p["sheetId"]}} for p in stages if p["sheetId"] in ids]
                if cleanup:
                    self.api.batch(self.target, cleanup, idempotent=False)
            except Exception:
                LOG.exception("Staging belum terhapus; dibersihkan otomatis pada refresh berikutnya.")
        if committed:
            progress(phase="done", message="Semua mitra dan sheet gabungan sudah diperbarui")

