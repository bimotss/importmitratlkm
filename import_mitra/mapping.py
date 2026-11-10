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
    start = max(source.get("start", 2) - 1, header_row + 1)
    rows = []
    for row in values[start:]:
        value = row[key_index] if len(row) > key_index else ""
        if value is None or str(value).strip() == "" or norm(value) in {"SITEID", "NEID"}:
            continue
        rows.append(list(row) + [""] * (width - len(row)))
    return SourceData(source, headers, keys, rows, key_index)


def project(data, headers, aliases):
    positions = {norm(k): i for i, k in enumerate(data.keys)}
    result = []
    for row in data.rows:
        out = []
        for header in headers:
            key = norm(canonical(header, data.config["group"], aliases))
            if key == "MITRA":
                value = data.name
            elif key == "FORADIO":
                value = data.config["group"]
            else:
                i = positions.get(key)
                value = row[i] if i is not None else ""
            out.append("" if value is None else value)
        result.append(out)
    return result


def partner_formula(data):
    index = data.key_index
    if data.config["group"] == "RADIO":
        index = next((i for i, h in enumerate(data.headers) if norm(h) == "HOP"), -1)
        if index < 0:
            raise ValueError(f"{data.config['title']}: judul HOP tidak ditemukan.")
    key = col(index + 2)  # kolom A di tujuan disediakan untuk MITRA
    name = data.name.replace('"', '""')
    return f'=ARRAYFORMULA(IF({key}2:{key}<>"","{name}",""))'

