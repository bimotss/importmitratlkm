"""Pemetaan mengikuti judul: urutan dan kolom tambahan sumber tidak menggeser hasil."""
import re
from dataclasses import dataclass


def norm(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def canonical(header, group, aliases):
    key = norm(header)
    if group == "RADIO" and key == "PROGRAM":
        return "TEMATIK"
    return aliases.get(key, " ".join(str(header).strip().upper().split()))


def col(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def quoted(title):
    return "'" + title.replace("'", "''") + "'"


@dataclass
class SourceData:
    config: dict
    headers: list
    keys: list
    rows: list
    key_index: int

    @property
    def name(self):
        return re.sub(r"^data_", "", self.config["title"], flags=re.IGNORECASE).upper()


def parse_source(values, source, aliases):
    # Partner-specific header changes must not redefine generic STATUS fields
    # for every other partner (or for Radio).
    aliases = {**aliases, **source.get("header_aliases", {})}
    candidates = []
    for i, row in enumerate(values[:10]):
        keys = [norm(h) for h in row]
        if "SITEID" in keys or "NEID" in keys:
            score = sum(k in aliases for k in keys if k)
            candidates.append((score, -i, i))
    if not candidates:
        raise ValueError(f"{source['title']}: judul SITE ID / NE ID tidak ditemukan pada 10 baris pertama.")
    header_row = max(candidates)[2]
    width = max((len(row) for row in values), default=0)
    raw = [str(h or "").strip() for h in values[header_row]]
    raw += [""] * (width - len(raw))
    headers, keys, used = [], [], {}
    for i, h in enumerate(raw):
        h = " ".join(h.split()) or f"{source['title']} | KOLOM {col(i + 1)}"
        base = canonical(h, source["group"], aliases)
        used[base] = used.get(base, 0) + 1
        occurrence = used[base]
        headers.append(h if occurrence == 1 else f"{h} [{occurrence}]")
        keys.append(base if occurrence == 1 else f"{source['title']} | {base} [{occurrence}]")
    key_index = next(i for i, h in enumerate(raw) if norm(h) in {"SITEID", "NEID"})
    missing = [h for h in source.get("required_headers", [])
               if norm(h) not in {norm(k) for k in keys}]
    if missing:
        raise ValueError(f"{source['title']}: kolom wajib hilang: {', '.join(missing)}; impor dibatalkan.")
    start = max(source.get("start", 2) - 1, header_row + 1)
    rows = []
    for row in values[start:]:
        value = row[key_index] if len(row) > key_index else ""
        if value is None or str(value).strip() == "" or norm(value) in {"SITEID", "NEID"}:
            continue
        rows.append(list(row) + [""] * (width - len(row)))
    return SourceData(source, headers, keys, rows, key_index)


def project(data, headers, aliases, aging_tracker=None):
    from .aging import is_order_2026

    positions = {norm(k): i for i, k in enumerate(data.keys)}
    group = data.config["group"].upper()
    result = []
    for row in data.rows:
        aging_days = None
        change_serial = None
        grouping = ""

        if aging_tracker is not None:
            if group == "RADIO":
                hop = row[positions["HOP"]] if "HOP" in positions else ""
                sp = row[positions["SP"]] if "SP" in positions else (row[positions["WOSP"]] if "WOSP" in positions else "")
                key = (hop, sp, data.name)
                flagging = row[positions["FLAGGINGPROGRESS"]] if "FLAGGINGPROGRESS" in positions else ""
                inhand_raw = row[positions["TGLORDERDITERIMA"]] if "TGLORDERDITERIMA" in positions else (
                    row[positions["INHANDDATE"]] if "INHANDDATE" in positions else ""
                )
                existing_change = row[positions["TANGGALGANTIFLAGGING"]] if "TANGGALGANTIFLAGGING" in positions else None
                is_2026 = is_order_2026(order_date_val=inhand_raw, sp_val=sp)
                aging_days, change_serial, grouping = aging_tracker.track_record(
                    "radio", key, flagging, is_2026=is_2026, existing_change_date=existing_change, order_date=inhand_raw
                )
            else:
                site = row[positions["SITEID"]] if "SITEID" in positions else ""
                sp = row[positions["SP"]] if "SP" in positions else (row[positions["WOSP"]] if "WOSP" in positions else "")
                key = (site, sp, data.name)
                flagging = row[positions["STATUSVERIFIKASICOD"]] if "STATUSVERIFIKASICOD" in positions else ""
                tahun_raw = row[positions["TAHUNPROJECT"]] if "TAHUNPROJECT" in positions else ""
                tgl_raw = row[positions["TGLORDERDITERIMA"]] if "TGLORDERDITERIMA" in positions else ""
                is_2026 = is_order_2026(tahun_project=tahun_raw, order_date_val=tgl_raw, sp_val=sp)

                existing_change = None
                norm_flag = norm(flagging)
                if "SURVEY" in norm_flag and "TANGGALFINISHSURVEY" in positions:
                    existing_change = row[positions["TANGGALFINISHSURVEY"]]
                elif "PERIZINAN" in norm_flag and "TANGGALFINISHPERIZINAN" in positions:
                    existing_change = row[positions["TANGGALFINISHPERIZINAN"]]
                elif ("MATDEL" in norm_flag or "MATERIAL" in norm_flag) and "TANGGALMATERIALONSITE" in positions:
                    existing_change = row[positions["TANGGALMATERIALONSITE"]]
                elif "INSTALASI" in norm_flag and "TANGGALFINISHINSTALASI" in positions:
                    existing_change = row[positions["TANGGALFINISHINSTALASI"]]

                aging_days, change_serial, grouping = aging_tracker.track_record(
                    "fo", key, flagging, is_2026=is_2026, existing_change_date=existing_change, order_date=tgl_raw
                )

        out = []
        for header in headers:
            key = norm(canonical(header, data.config["group"], aliases))
            if key == "MITRA":
                value = data.name
            elif key == "FORADIO":
                value = data.config["group"]
            elif key == "FLAGGINGFORADIO":
                source_key = "STATUSVERIFIKASICOD" if data.config["group"] == "FO" else "FLAGGINGPROGRESS"
                i = positions.get(source_key)
                value = combined_flagging(row[i] if i is not None else "", data.config["group"])
            elif key == "AGINGPROGRESS" and aging_tracker is not None:
                value = aging_days if aging_days is not None else ""
            elif key == "GROUPINGAGINGPROGRESS" and aging_tracker is not None:
                value = grouping
            elif key == "TANGGALGANTIFLAGGING" and aging_tracker is not None:
                value = change_serial if change_serial is not None else ""
            else:
                i = positions.get(key)
                value = row[i] if i is not None else ""
            out.append("" if value is None else value)
        result.append(out)
    return result


def combined_flagging(value, group):
    """Use COD's common progress labels; retain unknown labels and source blanks."""
    if value in (None, ""):
        return ""
    if group == "FO":
        return {"02.OGP SURVEY": "02.OGP SURVEY / MATERIAL PREPARATION"}.get(value, value)
    return {
        "00.DROP": "00.DROP SMILE",
        "02.MATERIAL PREPARATION": "02.OGP SURVEY / MATERIAL PREPARATION",
        "03.PERIZINAN": "04.PERIZINAN",
        "04.MATERIAL DELIVERY": "05.MATDEL",
        "05.INSTALASI": "07.INSTALASI",
        "06.RFS": "08.RFS",
        "07.CT": "09.CT",
        "08.UT": "10.UT",
        "09.BAST": "11.BAST",
    }.get(value, value)


def partner_formula(data):
    index = data.key_index
    if data.config["group"] == "RADIO":
        index = next((i for i, h in enumerate(data.headers) if norm(h) == "HOP"), -1)
        if index < 0:
            raise ValueError(f"{data.config['title']}: judul HOP tidak ditemukan.")
    key = col(index + 2)  # kolom A di tujuan disediakan untuk MITRA
    name = data.name.replace('"', '""')
    return f'=ARRAYFORMULA(IF({key}2:{key}<>"","{name}",""))'

