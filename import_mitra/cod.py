"""Refresh COD 2026 while retaining headers, columns, helper formulas and row identity."""
import gzip
import json
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from .importer import value_chunks
from .mapping import canonical, col, norm, quoted

LOG = logging.getLogger(__name__)
STAGE = re.compile(r"^_IMCOD_[a-f0-9]{12}_[012]$")


def business_key(values):
    if not values or values[0] in (None, ""):
        return None
    if str(values[0]).startswith(("#REF!", "#N/A", "#VALUE!", "#ERROR!")):
        raise ValueError("Kolom kunci COD mengandung error; perbaiki sebelum mengganti data.")
    return tuple(norm(v) for v in values)


def keep_row_positions(records, old_keys):
    """Keep existing identities in place. New identities append; removed ones leave blank slots."""
    queues = defaultdict(deque)
    for index, record in enumerate(records):
        queues[record["key"]].append(index)
    last = max((i + 1 for i, k in enumerate(old_keys) if k is not None), default=0)
    slots, consumed = [None] * last, set()
    retained = 0
    for i, key in enumerate(old_keys[:last]):
        if key is not None and queues[key]:
            index = queues[key].popleft()
            slots[i] = records[index]
            consumed.add(index)
            retained += 1
    existing = sum(k is not None for k in old_keys)
    if existing and retained < existing / 2:
        raise ValueError(f"Hanya {retained}/{existing} kunci lama cocok; hentikan untuk memeriksa pemetaan.")
    added = [record for i, record in enumerate(records) if i not in consumed]
    slots.extend(added)
    return slots, {"retained": retained, "added": len(added), "removed": existing - retained,
                   "rows": len(records), "last_row": len(slots) + 1, "append_start": last + 2}


def project_records(datasets, spec, aliases, group):
    records = []
    available = set()
    for data in datasets:
        if data.config["group"] != group:
            continue
        for row in data.rows:
            fields = {norm(k): row[i] for i, k in enumerate(data.keys)}
            fields["MITRA"] = data.name
            available.update(fields)
            def value(header, fields=fields):
                return fields.get(norm(canonical(header, group, aliases)), "")
            values = []
            for number in spec["managed_columns"]:
                candidates = spec.get("overrides", {}).get(str(number), [spec["headers"][number - 1]])
                found = [value(h) for h in candidates]
                values.append(next((v for v in found if v not in (None, "")), ""))
            site, sp = value("SITE ID"), value("SP")
            first = value("HOP") if group == "RADIO" else site
            if first in (None, ""):
                raise ValueError(f"{data.config['title']}: kunci SITE ID / HOP kosong; perlu diperiksa.")
            records.append({"key": business_key([first, sp, data.name]), "values": values,
                            "combined_key": business_key([site, sp, data.name])})
    missing = []
    for number in spec["managed_columns"]:
        candidates = spec.get("overrides", {}).get(str(number), [spec["headers"][number - 1]])
        if not any(norm(canonical(h, group, aliases)) in available for h in candidates):
            missing.append(spec["headers"][number - 1])
    return records, missing


def column_runs(columns):
    runs = []
    for source, target in enumerate(columns):
        if runs and runs[-1][1] + runs[-1][2] == target - 1:
            start, dest, length = runs[-1]
            runs[-1] = (start, dest, length + 1)
        else:
            runs.append((source, target - 1, 1))
    return runs


def remap_extra_values(old_keys, entered_rows, new_keys, width):
    queues = defaultdict(deque)
    for i, key in enumerate(old_keys):
        if key is None:
            continue
        row = entered_rows[i].get("values", []) if i < len(entered_rows) else []
        values = []
        for j in range(width):
            cell = row[j].get("userEnteredValue", {}) if j < len(row) else {}
            if "formulaValue" in cell:
                raise ValueError("Kolom tambahan DataDisatukan mengandung formula; perlu review sebelum dipindah.")
            values.append(next(iter(cell.values()), ""))
        queues[key].append(values)
    return [queues[k].popleft() if queues[k] else [""] * width for k in new_keys]


class CODSync:
    def __init__(self, api, config, profile, state_dir, backup_keep=24):
        self.api, self.config, self.profile = api, config, profile
        self.target, self.state_dir = profile["spreadsheet_id"], state_dir
        self.backup_keep = max(1, backup_keep)
        if self.target == config["target_spreadsheet_id"]:
            raise ValueError("COD dan file rekap harus berbeda.")

    def heads(self):
        meta = self.api.metadata(self.target)
        by_name = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
        heads = {}
        for key, spec in self.profile["groups"].items():
            p = by_name.get(spec["title"])
            if not p or p["sheetId"] != spec["sheet_id"]:
                raise ValueError(f"Tab COD tidak cocok: {spec['title']}")
            if p["gridProperties"]["columnCount"] != len(spec["headers"]):
                raise ValueError(f"Jumlah kolom berubah pada {spec['title']}; perlu review.")
            rows = self.api.cells(self.target, f"{quoted(spec['title'])}!A1:{col(len(spec['headers']))}2",
                                  effective=True)
            header_cells = rows[0].get("values", [])
            labels = [c.get("effectiveValue", {}).get("stringValue", "") for c in header_cells]
            labels += [""] * (len(spec["headers"]) - len(labels))
            if labels != spec["headers"]:
                raise ValueError(f"Judul/urutan kolom berubah pada {spec['title']}; tidak menulis data.")
            heads[key] = {"properties": p, "header_cells": header_cells,
                          "templates": rows[1].get("values", []) if len(rows) > 1 else []}
        return meta, heads

    def old_keys(self, heads):
        ranges = []
        for key, spec in self.profile["groups"].items():
            height = heads[key]["properties"]["gridProperties"]["rowCount"]
            ranges.extend(f"{quoted(spec['title'])}!{col(c)}2:{col(c)}{height}" for c in spec["key_columns"])
        values = iter(self.api.read_many(self.target, ranges))
        result = {}
        for key in self.profile["groups"]:
            columns = [next(values).get("values", []) for _ in range(3)]
            height = max(map(len, columns), default=0)
            result[key] = [business_key([c[i][0] if i < len(c) and c[i] else "" for c in columns])
                           for i in range(height)]
        return result

    def prepare(self, datasets):
        meta, heads = self.heads()
        old = self.old_keys(heads)
        plans, summary = {}, {}
        for key in ("fo", "radio"):
            spec = self.profile["groups"][key]
            records, missing = project_records(datasets, spec, self.config["aliases"], key.upper())
            slots, stats = keep_row_positions(records, old[key])
            plans[key] = {"rows": [r["values"] if r else [""] * len(spec["managed_columns"]) for r in slots],
                          "records": slots, "stats": stats}
            summary[key] = {**stats, "unavailable_source_fields": missing}
            # Never leave an old source-stack formula pointing at the old partner mirrors.
            for i, cell in enumerate(heads[key]["templates"], 1):
                formula = cell.get("userEnteredValue", {}).get("formulaValue", "")
                if re.search(r"Data_(TA|PST|BTO|CMM_ZTE|Huawei|Aviat|PST_Radio|Multireka)!", formula,
                             re.IGNORECASE) and i not in spec["managed_columns"]:
                    raise ValueError(f"Rumus sumber lama belum ditangani: {spec['title']}!{col(i)}2")
        combined = [r["combined_key"] for key in ("fo", "radio") for r in plans[key]["records"] if r]
        spec = self.profile["groups"]["all"]
        extra = spec["managed_columns"]
        height = heads["all"]["properties"]["gridProperties"]["rowCount"]
        entered = self.api.cells(self.target, f"{quoted(spec['title'])}!{col(extra[0])}2:{col(extra[-1])}{height}")
        plans["all"] = {"rows": remap_extra_values(old["all"], entered, combined, len(extra)),
                        "stats": {"last_row": len(combined) + 1, "rows": len(combined)}}
        summary["total"] = len(combined)
        summary["columns"] = {k: len(s["headers"]) for k, s in self.profile["groups"].items()}
        summary["preserve_columns"] = True
        return meta, heads, plans, summary

    def backup(self, heads):
        folder = self.state_dir / "cod-backups"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        completed = folder / f"cod-{stamp}-{secrets.token_hex(4)}.jsonl.gz"
        path = completed.with_suffix(".gz.partial")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as stream:
            def save(record):
                stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode())
            save({"profile": self.profile, "properties": {k: h["properties"] for k, h in heads.items()}})
            for key, spec in self.profile["groups"].items():
                height = heads[key]["properties"]["gridProperties"]["rowCount"]
                LOG.info("Backup COD: %s", spec["title"])
                for first in range(1, height + 1, 400):
                    last = min(first + 399, height)
                    rows = self.api.cells(self.target, f"{quoted(spec['title'])}!A{first}:{col(len(spec['headers']))}{last}")
                    save({"group": key, "start_row": first, "rows": rows})
        path.replace(completed)
        for old in sorted(folder.glob("cod-*.jsonl.gz"), key=lambda p: p.stat().st_mtime, reverse=True)[self.backup_keep:]:
            old.unlink()
        return str(completed)

    def run(self, datasets, progress=lambda **kwargs: None, *, dry_run=False):
        progress(phase="cod_planning", message="Memeriksa kolom dan rumus COD 2026")
        meta, heads, plans, summary = self.prepare(datasets)
        if dry_run:
            return {**summary, "dry_run": True}
        progress(phase="cod_backup", message="Menyimpan backup COD sebelum pembaruan")
        summary["backup"] = self.backup(heads)
        self.publish(meta, heads, plans, progress)
        # Native formulas may require a short recalculation delay.
        ranges = []
        for key, c in (("fo", 2), ("radio", 11), ("all", 1)):
            spec = self.profile["groups"][key]
            end = max(plans[key]["stats"]["last_row"], heads[key]["properties"]["gridProperties"]["rowCount"])
            ranges.append(f"{quoted(spec['title'])}!{col(c)}2:{col(c)}{end}")
        expected = [summary["fo"]["rows"], summary["radio"]["rows"], summary["total"]]
        for attempt in range(4):
            self.heads()
            actual = self.api.read_many(self.target, ranges)
            counts = [sum(bool(r and r[0] not in (None, "")) for r in v.get("values", [])) for v in actual]
            if counts == expected:
                LOG.info("COD selesai: FO %s, RADIO %s, total %s; semua kolom tetap.", *counts)
                return summary
            time.sleep(3 * (attempt + 1))
        raise ValueError(f"COD sudah ditulis tetapi rekonsiliasi perlu diperiksa: {counts}, expected {expected}; backup {summary['backup']}")

    def publish(self, meta, heads, plans, progress):
        stale = [s["properties"]["sheetId"] for s in meta["sheets"]
                 if STAGE.fullmatch(s["properties"]["title"])]
        if stale:
            self.api.batch(self.target, [{"deleteSheet": {"sheetId": i}} for i in stale], idempotent=False)
        used = {s["properties"]["sheetId"] for s in meta["sheets"]}
        run_id, stages = secrets.token_hex(6), {}
        for i, (key, spec) in enumerate(self.profile["groups"].items()):
            while (sheet_id := secrets.randbelow(2_000_000_000) + 1) in used:
                pass
            used.add(sheet_id)
            stages[key] = {"sheetId": sheet_id, "title": f"_IMCOD_{run_id}_{i}", "hidden": True,
                           "gridProperties": {"rowCount": max(2, len(plans[key]["rows"]) + 1),
                                              "columnCount": len(spec["managed_columns"])}}
        allocated = sum(s["properties"]["gridProperties"]["rowCount"] * s["properties"]["gridProperties"]["columnCount"]
                        for s in meta["sheets"] if s["properties"]["sheetId"] not in stale)
        staging_cells = sum(p["gridProperties"]["rowCount"] * p["gridProperties"]["columnCount"] for p in stages.values())
        growth = sum(max(0, plans[k]["stats"]["last_row"] - heads[k]["properties"]["gridProperties"]["rowCount"])
                     * len(s["headers"]) for k, s in self.profile["groups"].items())
        if allocated + staging_cells + growth > 10_000_000:
            raise ValueError("COD tidak cukup ruang untuk staging; tidak mengubah data.")
        try:
            self.api.batch(self.target, [{"addSheet": {"properties": p}} for p in stages.values()], idempotent=False)
            for key, stage in stages.items():
                spec = self.profile["groups"][key]
                progress(phase="cod_staging", message=f"Menyiapkan {spec['title']}")
                LOG.info("Staging COD: %s", spec["title"])
                rows = [[spec["headers"][c - 1] for c in spec["managed_columns"]]] + plans[key]["rows"]
                for first, chunk in value_chunks(rows):
                    self.api.write(self.target, f"{quoted(stage['title'])}!A{first}", chunk)
            staged_headers = self.api.read_many(self.target, [
                f"{quoted(stages[k]['title'])}!A1:{col(len(s['managed_columns']))}1"
                for k, s in self.profile["groups"].items()])
            expected_headers = [[s["headers"][c - 1] for c in s["managed_columns"]]
                                for s in self.profile["groups"].values()]
            if [v.get("values", [[]])[0] for v in staged_headers] != expected_headers:
                raise ValueError("Header staging COD tidak cocok; hasil lama tidak diganti.")
            # Header and anchor formulas must still be exactly what was inspected.
            _, current = self.heads()
            for key in heads:
                if current[key]["header_cells"] != heads[key]["header_cells"]:
                    raise ValueError("Header COD berubah saat persiapan; pembaruan dibatalkan.")
            requests = []
            for key, spec in self.profile["groups"].items():
                sid = spec["sheet_id"]
                old_height = heads[key]["properties"]["gridProperties"]["rowCount"]
                height = max(old_height, plans[key]["stats"]["last_row"], 2)
                if height > old_height:
                    requests.append({"updateSheetProperties": {"properties": {"sheetId": sid,
                        "gridProperties": {"rowCount": height}}, "fields": "gridProperties.rowCount"}})
                for source, dest, length in column_runs(spec["managed_columns"]):
                    area = {"sheetId": sid, "startRowIndex": 1, "endRowIndex": height,
                            "startColumnIndex": dest, "endColumnIndex": dest + length}
                    requests.append({"updateCells": {"range": area, "fields": "userEnteredValue"}})
                    if plans[key]["rows"]:
                        requests.append({"copyPaste": {"source": {"sheetId": stages[key]["sheetId"],
                            "startRowIndex": 1, "endRowIndex": len(plans[key]["rows"]) + 1,
                            "startColumnIndex": source, "endColumnIndex": source + length},
                            "destination": {**area, "endRowIndex": len(plans[key]["rows"]) + 1},
                            "pasteType": "PASTE_VALUES", "pasteOrientation": "NORMAL"}})
                if key != "all" and plans[key]["stats"]["added"]:
                    start = plans[key]["stats"]["append_start"] - 1
                    end = plans[key]["stats"]["last_row"]
                    for index, cell in enumerate(heads[key]["templates"]):
                        formula = cell.get("userEnteredValue", {}).get("formulaValue", "")
                        if index + 1 in spec["managed_columns"] or not formula:
                            continue
                        if re.search(r"\b(ARRAYFORMULA|LET|MAP|FILTER|GEMINI|AI|IMPORTRANGE)\s*\(", formula, re.IGNORECASE):
                            continue
                        requests.append({"copyPaste": {"source": {"sheetId": sid, "startRowIndex": 1,
                            "endRowIndex": 2, "startColumnIndex": index, "endColumnIndex": index + 1},
                            "destination": {"sheetId": sid, "startRowIndex": start, "endRowIndex": end,
                                            "startColumnIndex": index, "endColumnIndex": index + 1},
                            "pasteType": "PASTE_FORMULA", "pasteOrientation": "NORMAL"}})
            progress(phase="cod_publishing", message="Memperbarui data COD tanpa mengubah kolom")
            self.api.batch(self.target, requests)
        finally:
            try:
                live = self.api.metadata(self.target)
                ids = {s["properties"]["sheetId"] for s in live["sheets"]}
                remove = [{"deleteSheet": {"sheetId": p["sheetId"]}} for p in stages.values() if p["sheetId"] in ids]
                if remove:
                    self.api.batch(self.target, remove, idempotent=False)
            except Exception:
                LOG.exception("Staging COD belum dibersihkan; dicoba lagi pada sinkronisasi berikutnya.")


class CODImporter:
    """JobRunner adapter sharing the existing lock, credentials and source reader."""
    def __init__(self, importer, sync):
        self.importer, self.sync = importer, sync

    def run(self, progress=lambda **kwargs: None, *, dry_run=False):
        datasets = self.importer.collect(progress)
        result = self.sync.run(datasets, progress, dry_run=dry_run)
        return {"fo": result["fo"]["rows"], "radio": result["radio"]["rows"], "total": result["total"],
                "sources": len(datasets), "tabs": 3, "dry_run": dry_run, "cod": result}


def make_cod_sync(settings, importer):
    profile = json.loads(settings.cod_profile.read_text())
    return CODSync(importer.api, importer.config, profile, settings.state_dir, settings.cod_backup_keep)


def make_cod_importer(settings):
    from .web import make_importer
    importer = make_importer(settings)
    return CODImporter(importer, make_cod_sync(settings, importer))
