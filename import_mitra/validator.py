"""Modul Validasi Kualitas Data Mitra untuk ImportMitra dan botCOD.

Fitur:
1. Validasi Nilai Dropdown (ONE_OF_LIST & ONE_OF_RANGE) dengan pencocokan eksak.
2. Validasi Kolom Kuning Wajib Isi untuk Mitra FO Baru setelah SITE ID terisi.
3. Ekstraksi referensi aturan yang sah dan penandaan unverified scope.
4. Publikasi laporan atomik ke runtime/validation-report.json dengan perlindungan konkurensi.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mapping import col, norm, quoted

LOG = logging.getLogger(__name__)

# Mitra FO yang dikecualikan DARI ATURAN KOLOM KUNING (dropdown tetap divalidasi)
EXCLUDED_FO_TITLES = {"ta", "bto", "pst"}


def utc_now_iso() -> str:
    """Format waktu UTC ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def is_new_fo_partner(source: dict) -> bool:
    """Periksa apakah sumber merupakan mitra FO baru (selain TA, BTO, dan PST)."""
    group = str(source.get("group", "")).strip().upper()
    title = str(source.get("title", "")).strip().lower()
    return group == "FO" and title not in EXCLUDED_FO_TITLES


def is_yellow_color(bg: dict | None) -> bool:
    """Deteksi apakah warna latar belakang sel adalah warna kuning (#FFFF00 / red~1, green~1, blue<0.2)."""
    if not isinstance(bg, dict):
        return False
    r = float(bg.get("red", 0.0))
    g = float(bg.get("green", 0.0))
    b = float(bg.get("blue", 0.0))
    # Kuning standar (#FFFF00): red >= 0.85, green >= 0.85, blue <= 0.25
    return r >= 0.85 and g >= 0.85 and b <= 0.25


@dataclass
class DropdownRule:
    rule_type: str  # ONE_OF_LIST or ONE_OF_RANGE
    allowed_values: list[str]
    is_definitive: bool
    source_reference: str | None = None


@dataclass
class Finding:
    finding_id: str
    mitra: str
    group: str
    spreadsheet_id: str
    sheet_id: int
    sheet_title: str
    row: int
    col: int
    col_letter: str
    col_name: str
    cell_address: str
    identifier: str
    identifier_type: str
    current_value: str
    finding_type: str  # "mandatory_empty" | "dropdown_mismatch"
    reason: str
    allowed_options: list[str] | None = None
    cell_url: str = ""
    is_priority: bool = False


class MitraValidator:
    def __init__(self, api, config: dict, report_path: Path | None = None):
        self.api = api
        self.config = config
        self.report_path = (
            report_path
            or Path(
                os.getenv("MITRA_VALIDATION_REPORT_PATH", "runtime/validation-report.json")
            )
        ).resolve()
        self._range_cache: dict[str, list[str]] = {}

    def parse_dropdown_rule(
        self, condition: dict, spreadsheet_id: str
    ) -> DropdownRule | None:
        """Ekstraksi daftar pilihan yang sah dari aturan dataValidation Google Sheets.
        
        Mengabaikan tipe non-dropdown seperti BOOLEAN (checkbox).
        """
        cond_type = condition.get("type", "")
        values = condition.get("values", [])

        # Abaikan validasi non-dropdown seperti checkbox
        if cond_type == "BOOLEAN":
            return None

        if cond_type == "ONE_OF_LIST":
            allowed = [
                str(v.get("userEnteredValue", ""))
                for v in values
                if v.get("userEnteredValue") is not None
            ]
            return DropdownRule(
                rule_type="ONE_OF_LIST",
                allowed_values=allowed,
                is_definitive=True,
                source_reference="literal_list",
            )

        if cond_type == "ONE_OF_RANGE":
            if not values:
                return DropdownRule("ONE_OF_RANGE", [], False, "empty_range_spec")
            raw_ref = values[0].get("userEnteredValue", "")
            clean_ref = raw_ref.lstrip("=").replace("$", "")
            
            cache_key = f"{spreadsheet_id}:{clean_ref}"
            if cache_key in self._range_cache:
                cached_vals = self._range_cache[cache_key]
                return DropdownRule(
                    rule_type="ONE_OF_RANGE",
                    allowed_values=cached_vals,
                    is_definitive=bool(cached_vals),
                    source_reference=clean_ref,
                )

            try:
                # Baca rentang referensi dari spreadsheet yang sama
                cells = self.api.read(spreadsheet_id, clean_ref)
                flattened = []
                for row in cells:
                    for val in row:
                        val_str = str(val).strip() if val is not None else ""
                        if val_str:
                            flattened.append(str(val))
                self._range_cache[cache_key] = flattened
                return DropdownRule(
                    rule_type="ONE_OF_RANGE",
                    allowed_values=flattened,
                    is_definitive=bool(flattened),
                    source_reference=clean_ref,
                )
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Gagal membaca referensi dropdown %s: %s", clean_ref, exc)
                return DropdownRule("ONE_OF_RANGE", [], False, f"error:{exc}")

        return None

    def inspect_source_schema(
        self, source: dict, raw_values: list[list[Any]] | None = None
    ) -> tuple[int, int, list[str], int, str, set[int], dict[int, DropdownRule], dict[tuple[int, int], DropdownRule], list[dict]]:
        """Membaca metadata header, format warna kuning, dan aturan dropdown sah per kolom.
        
        Returns:
            sheet_id, header_row_idx, headers, key_index, identifier_label, yellow_column_indices, column_rules, cell_rules, unverified_scopes
        """
        doc_id = source["id"]
        sheet_title = source["sheet"]
        group = str(source.get("group", "FO")).strip().upper()
        unverified_scopes: list[dict] = []

        # Bound metadata to occupied cells, and split large sheets so repeated
        # dropdown definitions do not create an oversized API response.
        if raw_values is None:
            raw_values = self.api.read(doc_id, quoted(sheet_title))
        height = max(10, len(raw_values))
        width = max(1, max((len(row) for row in raw_values), default=1))
        sheet_range = quoted(sheet_title)
        first_end = min(height, 500)
        res = self.api.request(
            "GET",
            doc_id,
            params={
                "ranges": [
                    f"{sheet_range}!A1:{col(width)}10",
                    f"{sheet_range}!A1:{col(width)}{first_end}",
                ],
                "includeGridData": "true",
                "fields": "sheets(properties(sheetId,title),data(rowData(values(formattedValue,effectiveFormat(backgroundColor),dataValidation))))",
            },
        )

        sheet_obj = res["sheets"][0]
        sheet_id = sheet_obj["properties"]["sheetId"]
        data_blocks = sheet_obj.get("data", [])

        first_10_rows = (
            data_blocks[0].get("rowData", [])
            if len(data_blocks) > 0
            else []
        )
        all_sheet_rows = (
            data_blocks[1].get("rowData", [])
            if len(data_blocks) > 1
            else first_10_rows
        )

        for first in range(501, height + 1, 500):
            last = min(first + 499, height)
            chunk = self.api.request(
                "GET", doc_id, params={
                    "ranges": [f"{sheet_range}!A{first}:{col(width)}{last}"],
                    "includeGridData": "true",
                    "fields": "sheets(data(startRow,rowData(values(dataValidation))))",
                },
            )
            blocks = chunk.get("sheets", [{}])[0].get("data", [])
            if not blocks:
                continue
            block = blocks[0]
            offset = block.get("startRow", first - 1)
            rows = block.get("rowData", [])
            if len(all_sheet_rows) < offset:
                all_sheet_rows.extend({} for _ in range(offset - len(all_sheet_rows)))
            all_sheet_rows.extend(rows)

        # 1. Deteksi Baris Header pada 10 baris pertama yang konsisten dengan importer
        aliases = {**self.config.get("aliases", {}), **source.get("header_aliases", {})}
        candidates = []
        for i, row_obj in enumerate(first_10_rows):
            cells = row_obj.get("values", [])
            keys = [norm(str(c.get("formattedValue", ""))) for c in cells]
            if "SITEID" in keys or "NEID" in keys or (group == "RADIO" and "HOP" in keys):
                score = sum(k in aliases for k in keys if k)
                candidates.append((score, -i, i))

        if not candidates:
            # Kolom identitas tidak ditemukan pada 10 baris pertama
            scope_reason = f"Kolom identitas ({'HOP / NE ID' if group == 'RADIO' else 'SITE ID / NE ID'}) tidak ditemukan pada 10 baris pertama"
            unverified_scopes.append({
                "source": source["title"],
                "scope": "header_and_identity",
                "reason": scope_reason,
            })
            return sheet_id, -1, [], -1, "", set(), {}, {}, unverified_scopes

        header_row_idx = max(candidates)[2]
        header_cells = (
            first_10_rows[header_row_idx].get("values", [])
            if header_row_idx < len(first_10_rows)
            else []
        )

        headers: list[str] = []
        yellow_indices: set[int] = set()
        is_new_fo = is_new_fo_partner(source)

        for c_idx, cell in enumerate(header_cells):
            val = " ".join(str(cell.get("formattedValue", "")).strip().split())
            headers.append(val or f"KOLOM {col(c_idx + 1)}")
            
            # Aturan B: Hanya periksa warna kuning untuk mitra FO baru pada baris header yang terdeteksi
            if is_new_fo:
                bg = cell.get("effectiveFormat", {}).get("backgroundColor", {})
                if is_yellow_color(bg):
                    yellow_indices.add(c_idx)

        # 2. Identifikasi Kolom Identitas Baris (SITE ID / HOP / NE ID)
        norm_headers = [norm(h) for h in headers]
        key_candidates = (
            ["HOP", "NEID", "SITEID"] if group == "RADIO" else ["SITEID", "NEID"]
        )
        key_index = -1
        identifier_label = ""
        for cand in key_candidates:
            if cand in norm_headers:
                key_index = norm_headers.index(cand)
                identifier_label = "HOP" if cand == "HOP" else ("NE ID" if cand == "NEID" else "SITE ID")
                break

        if key_index < 0:
            unverified_scopes.append({
                "source": source["title"],
                "scope": "identity_column",
                "reason": f"Kolom identitas ({' / '.join(key_candidates)}) tidak ditemukan pada baris header {header_row_idx + 1}",
            })
            return sheet_id, header_row_idx, headers, -1, "", yellow_indices, {}, {}, unverified_scopes

        # 3. Kumpulkan Aturan Dropdown (sheet-wide & baris data)
        column_rule_candidates: dict[int, list[DropdownRule]] = {}
        cell_rules: dict[tuple[int, int], DropdownRule] = {}

        for r_idx, r_obj in enumerate(all_sheet_rows):
            cells = r_obj.get("values", [])
            for c_idx, cell in enumerate(cells):
                dv = cell.get("dataValidation")
                if not dv or not dv.get("condition"):
                    continue
                rule = self.parse_dropdown_rule(dv["condition"], doc_id)
                if rule is None:
                    continue  # Bukan dropdown (e.g. checkbox)
                
                cell_rules[(r_idx, c_idx)] = rule
                if c_idx not in column_rule_candidates:
                    column_rule_candidates[c_idx] = []
                column_rule_candidates[c_idx].append(rule)

        # Tentukan aturan sah per kolom dan deteksi ambiguitas
        column_rules: dict[int, DropdownRule] = {}
        for c_idx, rules in column_rule_candidates.items():
            definitive_rules = [r for r in rules if r.is_definitive]
            if not definitive_rules:
                # Seluruh rule untuk kolom ini gagal dibaca/tidak definitif
                h_name = headers[c_idx] if c_idx < len(headers) else f"Kolom {col(c_idx + 1)}"
                unverified_scopes.append({
                    "source": source["title"],
                    "column": h_name,
                    "col_letter": col(c_idx + 1),
                    "reason": f"Aturan dropdown tidak dapat dipastikan ({rules[0].source_reference})",
                })
                continue

            # Periksa apakah semua aturan definitif dalam kolom seragam
            first_allowed = tuple(definitive_rules[0].allowed_values)
            is_uniform = all(tuple(r.allowed_values) == first_allowed for r in definitive_rules)
            if is_uniform:
                # Kolom memiliki aturan seragam yang sah (bisa dipropagasi jika ada sel copas tanpa rule)
                column_rules[c_idx] = definitive_rules[0]
            else:
                # Terdapat aturan berbeda per baris dalam satu kolom yang ambigu
                h_name = headers[c_idx] if c_idx < len(headers) else f"Kolom {col(c_idx + 1)}"
                unverified_scopes.append({
                    "source": source["title"],
                    "column": h_name,
                    "col_letter": col(c_idx + 1),
                    "reason": "Aturan dropdown berbeda-beda pada kolom yang sama tanpa referensi tunggal",
                })

        return (
            sheet_id,
            header_row_idx,
            headers,
            key_index,
            identifier_label,
            yellow_indices,
            column_rules,
            cell_rules,
            unverified_scopes,
        )

    def validate_source(
        self, source: dict, raw_values: list[list[Any]] | None = None
    ) -> tuple[list[Finding], list[dict], int]:
        """Validasi data pada satu sumber spreadsheet mitra menggunakan snapshot mentah."""
        if raw_values is None:
            raw_values = self.api.read(source["id"], quoted(source["sheet"]))
        (
            sheet_id,
            header_row_idx,
            headers,
            key_index,
            identifier_label,
            yellow_indices,
            column_rules,
            cell_rules,
            unverified,
        ) = self.inspect_source_schema(source, raw_values=raw_values)

        # Jika kolom identitas atau header tidak dapat ditentukan, jangan tebak kolom 0
        if key_index < 0 or header_row_idx < 0:
            return [], unverified, len(raw_values)

        group = source.get("group", "FO").upper()
        is_new_fo = is_new_fo_partner(source)

        findings: list[Finding] = []
        # Tentukan baris awal data berdasarkan konfigurasi atau baris setelah header
        configured_start = source.get("start", header_row_idx + 2)
        start_row_num = max(configured_start, header_row_idx + 2)
        start_row_idx = max(start_row_num - 1, header_row_idx + 1)

        # Kumpulkan semua kolom yang wajib divalidasi dropdown
        dropdown_cols = set(column_rules) | {c for _, c in cell_rules}

        for r_idx in range(start_row_idx, len(raw_values)):
            row = raw_values[r_idx]
            actual_row_num = r_idx + 1  # 1-indexed row number asli di Google Sheets

            # Ambil nilai identitas baris
            raw_id = (
                row[key_index]
                if len(row) > key_index and row[key_index] is not None
                else ""
            )
            id_val = str(raw_id).strip()

            # Aturan B: Mitra FO baru & Kolom Kuning Wajib
            # Hanya berlaku jika SITE ID terisi (bukan whitespace atau kosong)
            if is_new_fo and id_val:
                for y_idx in yellow_indices:
                    if y_idx == key_index:
                        continue
                    y_raw = (
                        row[y_idx]
                        if len(row) > y_idx and row[y_idx] is not None
                        else ""
                    )
                    y_str = str(y_raw).strip()
                    if not y_str:
                        # Wajib isi namun kosong/whitespace
                        col_name = (
                            headers[y_idx]
                            if y_idx < len(headers)
                            else f"Kolom {col(y_idx + 1)}"
                        )
                        c_letter = col(y_idx + 1)
                        c_addr = f"{c_letter}{actual_row_num}"
                        c_url = f"https://docs.google.com/spreadsheets/d/{source['id']}/edit#gid={sheet_id}&range={c_addr}"
                        findings.append(
                            Finding(
                                finding_id=f"{source['title']}_{actual_row_num}_{c_letter}",
                                mitra=source["title"],
                                group=group,
                                spreadsheet_id=source["id"],
                                sheet_id=sheet_id,
                                sheet_title=source["sheet"],
                                row=actual_row_num,
                                col=y_idx + 1,
                                col_letter=c_letter,
                                col_name=col_name,
                                cell_address=c_addr,
                                identifier=id_val,
                                identifier_type=identifier_label,
                                current_value="",
                                finding_type="mandatory_empty",
                                reason=f'Kolom kuning wajib "{col_name}" belum diisi setelah {identifier_label} terisi',
                                allowed_options=None,
                                cell_url=c_url,
                                is_priority=False,
                            )
                        )

            # Aturan A: Validasi Dropdown
            # Berlaku untuk SEMUA mitra (FO baru, TA, BTO, PST, dan RADIO)
            # Berlaku pada SEMUA baris data, termasuk yang SITE ID-nya kosong!
            for dv_idx in dropdown_cols:
                # Gunakan aturan spesifik sel jika ada, atau aturan seragam kolom jika sel kehilangan aturan (copas)
                rule = cell_rules.get((r_idx, dv_idx)) or column_rules.get(dv_idx)
                if not rule or not rule.is_definitive:
                    continue

                raw_val = (
                    row[dv_idx]
                    if len(row) > dv_idx and row[dv_idx] is not None
                    else ""
                )
                val_str = str(raw_val)
                trimmed_val = val_str.strip()

                # Sel kosong tidak otomatis dianggap kesalahan dropdown
                # (Jika kolom kuning wajib kosong, sudah dilaporkan di mandatory_empty di atas)
                if not trimmed_val:
                    continue

                # Nilai terisi harus sama persis dengan salah satu pilihan dropdown yang sah.
                # Jangan memangkas spasi atau mengubah kapitalisasi!
                if val_str not in rule.allowed_values:
                    col_name = (
                        headers[dv_idx]
                        if dv_idx < len(headers)
                        else f"Kolom {col(dv_idx + 1)}"
                    )
                    c_letter = col(dv_idx + 1)
                    c_addr = f"{c_letter}{actual_row_num}"
                    c_url = f"https://docs.google.com/spreadsheets/d/{source['id']}/edit#gid={sheet_id}&range={c_addr}"
                    
                    # Prioritas: RADIO atau kolom Status Verifikasi COD
                    is_prio = (
                        group == "RADIO"
                        or ("VERIFIKASI" in norm(col_name) and "COD" in norm(col_name))
                        or "STATUSVERIFIKASICOD" in norm(col_name)
                    )

                    findings.append(
                        Finding(
                            finding_id=f"{source['title']}_{actual_row_num}_{c_letter}",
                            mitra=source["title"],
                            group=group,
                            spreadsheet_id=source["id"],
                            sheet_id=sheet_id,
                            sheet_title=source["sheet"],
                            row=actual_row_num,
                            col=dv_idx + 1,
                            col_letter=c_letter,
                            col_name=col_name,
                            cell_address=c_addr,
                            identifier=id_val or f"Baris {actual_row_num}",
                            identifier_type=identifier_label or "BARIS",
                            current_value=val_str,
                            finding_type="dropdown_mismatch",
                            reason=f'Nilai "{val_str}" tidak sesuai pilihan dropdown ({len(rule.allowed_values)} pilihan sah)',
                            allowed_options=rule.allowed_values,
                            cell_url=c_url,
                            is_priority=is_prio,
                        )
                    )

        return findings, unverified, len(raw_values)

    def run(
        self,
        progress=lambda **kwargs: None,
        cached_datasets: list[Any] | None = None,
    ) -> dict:
        """Menjalankan validasi menyeluruh pada semua sumber dan menyimpan laporan secara atomik."""
        started_at = utc_now_iso()
        sources = self.config.get("sources", [])
        total_sources = len(sources)

        all_findings: list[Finding] = []
        all_unverified: list[dict] = []
        failed_sources: list[dict] = []
        checked_count = 0

        # Mapping cached datasets bila validator dipanggil saat proses impor
        dataset_by_title = {}
        if cached_datasets:
            for d in cached_datasets:
                title = d.config.get("title") if hasattr(d, "config") else None
                if title:
                    dataset_by_title[title] = d

        for i, source in enumerate(sources):
            title = source["title"]
            group = source.get("group", "FO")
            LOG.info("[%s/%s] Memvalidasi sumber %s...", i + 1, total_sources, title)
            progress(
                phase="validating",
                message=f"Memvalidasi {title}",
                source=title,
                completed_sources=i,
                total_sources=total_sources,
            )

            try:
                raw_vals = None
                if title in dataset_by_title:
                    # Gunakan snapshot mentah (raw_values) sebelum filtering
                    raw_vals = getattr(dataset_by_title[title], "raw_values", None)

                findings, unverified, row_count = self.validate_source(
                    source, raw_values=raw_vals
                )
                all_findings.extend(findings)
                all_unverified.extend(unverified)
                checked_count += 1
                LOG.info(
                    "Sumber %s OK: %s baris diperiksa, %s temuan",
                    title,
                    row_count,
                    len(findings),
                )
            except Exception as exc:
                LOG.exception("Gagal memvalidasi sumber %s", title)
                failed_sources.append(
                    {
                        "source": title,
                        "group": group,
                        "spreadsheet_id": source.get("id", ""),
                        "sheet": source.get("sheet", ""),
                        "error": str(exc),
                    }
                )

        finished_at = utc_now_iso()

        # Tentukan status akhir:
        # Hanya "success" jika seluruh sumber sukses diperiksa DAN tidak ada unverified_scopes!
        if checked_count == total_sources and not failed_sources and not all_unverified:
            status = "success"
        elif checked_count > 0:
            status = "partial"
        else:
            status = "failed"

        # Rekap metrik
        problematic_mitra = {f.mitra for f in all_findings}
        problematic_rows = {(f.mitra, f.row) for f in all_findings}
        findings_by_type = {
            "mandatory_empty": sum(
                1 for f in all_findings if f.finding_type == "mandatory_empty"
            ),
            "dropdown_mismatch": sum(
                1 for f in all_findings if f.finding_type == "dropdown_mismatch"
            ),
        }
        findings_by_group = {
            "FO": sum(1 for f in all_findings if f.group == "FO"),
            "RADIO": sum(1 for f in all_findings if f.group == "RADIO"),
        }
        total_priority_findings = sum(1 for f in all_findings if f.is_priority)

        # Urutkan temuan: prioritaskan temuan prioritas (RADIO dan Verifikasi COD), lalu nama mitra & nomor baris
        all_findings.sort(key=lambda f: (not f.is_priority, f.mitra, f.row, f.col))

        report = {
            "run_id": str(uuid.uuid4()),
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "total_sources": total_sources,
            "checked_sources": checked_count,
            "failed_sources": failed_sources,
            "unverified_scopes": all_unverified,
            "summary": {
                "total_findings": len(all_findings),
                "total_priority_findings": total_priority_findings,
                "total_problematic_mitra": len(problematic_mitra),
                "total_problematic_rows": len(problematic_rows),
                "findings_by_type": findings_by_type,
                "findings_by_group": findings_by_group,
            },
            "findings": [asdict(f) for f in all_findings],
        }

        # Publikasikan atomik
        self._publish_atomic(report)
        progress(
            phase="validation_done",
            message=f"Validasi selesai: {len(all_findings)} temuan dari {checked_count} mitra.",
        )
        return report

    def _publish_atomic(self, report: dict) -> None:
        """Simpan laporan secara atomik dengan izin 0644 dan pencegahan overwrite dari run lama."""
        self.report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)

        # Proteksi run lama: jika sudah ada file laporan dengan finished_at lebih baru, jangan timpa
        if self.report_path.exists():
            try:
                existing_data = json.loads(self.report_path.read_text(encoding="utf-8"))
                existing_finished = existing_data.get("finished_at", "")
                current_finished = report.get("finished_at", "")
                if existing_finished and current_finished and existing_finished > current_finished:
                    LOG.warning(
                        "Laporan validasi yang ada lebih baru (%s > %s). Publikasi dilewati.",
                        existing_finished,
                        current_finished,
                    )
                    return
            except Exception as read_exc:  # noqa: BLE001
                LOG.warning("Tidak dapat memeriksa timestamp laporan yang ada: %s", read_exc)

        # Tulis ke file temporer unik
        tmp_name = f".{self.report_path.name}.{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp"
        tmp_path = self.report_path.parent / tmp_name
        tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            tmp_path.chmod(0o644)
        except OSError:
            pass
        tmp_path.replace(self.report_path)
        LOG.info("Laporan validasi atomik dipublikasikan ke %s", self.report_path)
