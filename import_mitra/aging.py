"""Track flagging changes and compute aging milestone days for 2026 orders."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .mapping import norm

JAKARTA_TZ = ZoneInfo("Asia/Jakarta")
EXCEL_EPOCH = date(1899, 12, 30)


def to_excel_serial(d: date) -> int:
    """Convert a Python date to an Excel/Google Sheets serial date integer."""
    return (d - EXCEL_EPOCH).days


def parse_date_value(val: Any) -> date | None:
    """Parse a date from serial number, date string, or datetime."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, (int, float)):
        # Excel serial date: 35000 is ~1995, 60000 is ~2064
        if 35000 <= val <= 60000:
            return EXCEL_EPOCH + timedelta(days=int(val))
        return None
    s = str(val).strip()
    if not s or s.startswith(("#", "=")):
        return None
    # Maybe numeric string: "46231" or "46231.345"
    try:
        f = float(s)
        if 35000 <= f <= 60000:
            return EXCEL_EPOCH + timedelta(days=int(f))
    except ValueError:
        pass
    # Try common date formats
    for fmt in (
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%d/%m/%y",
        "%Y/%m/%d",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(s.split()[0] if " " in s and fmt.count(" ") == 0 else s, fmt).date()
        except ValueError:
            pass
    return None


def is_order_2026(
    tahun_project: Any = None,
    order_date_val: Any = None,
    sp_val: Any = None,
    fallback_true_if_unspecified: bool = True,
) -> bool:
    """Determine if a record belongs to order year 2026.

    Returns True if:
    - tahun_project is 2026 / '2026'
    - order_date year is 2026 (or serial 46023..46387)
    - SP / dasar_order contains 2026 / '26'
    Returns False if explicitly marked with another year (e.g. 2024, 2025).
    If no year indicator is present and fallback_true_if_unspecified is True,
    returns True (since both workbooks are 2026-scoped).
    """
    # 1. Explicit tahun_project
    if tahun_project is not None and str(tahun_project).strip():
        tp_str = str(tahun_project).strip()
        if tp_str in ("2026", "26") or tp_str.endswith("2026"):
            return True
        if re.fullmatch(r"20\d\d", tp_str) and tp_str != "2026":
            return False

    # 2. Check order date
    d = parse_date_value(order_date_val)
    if d is not None:
        if d.year == 2026:
            return True
        if d.year < 2026 or d.year > 2026:
            return False

    # 3. Check SP or contract label
    if sp_val:
        sp_str = str(sp_val).upper()
        if "2026" in sp_str or re.search(r"\b26\b", sp_str):
            return True
        for other_yr in ("2023", "2024", "2025", "2027", "2028"):
            if other_yr in sp_str:
                return False

    return fallback_true_if_unspecified


def grouping_aging_fo(days: int | None) -> str:
    """Grouping aging daily for FO (matching COD 2026 formula in Data TIF FO)."""
    if days is None or days < 0:
        return ""
    if days < 7:
        return "<1 minggu"
    if days <= 28:
        return "<1 minggu sd <4 minggu"
    return ">1 bulan"


def grouping_aging_radio(days: int | None) -> str:
    """Grouping aging progress for Radio (matching COD 2026 in Data TIF Radio)."""
    if days is None or days < 0:
        return ""
    if days <= 30:
        return "≤ 30 Days"
    return "> 30 Days"


class AgingTracker:
    """Stateful tracker for progress flagging changes and milestone aging calculation."""

    def __init__(self, state_path: str | Path | None = None, today: date | None = None):
        self.state_path = Path(state_path) if state_path else None
        self.today = today or datetime.now(JAKARTA_TZ).date()
        self.state: dict[str, dict[str, Any]] = {"fo": {}, "radio": {}}
        self._load()

    def _load(self):
        if not self.state_path or not self.state_path.exists():
            return
        # A broken history must fail the job, never silently reset milestone dates.
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("Format aging state tidak valid.")
        for group in ("fo", "radio"):
            bucket = data.get(group, {})
            if not isinstance(bucket, dict):
                raise TypeError(f"Format aging state {group} tidak valid.")
            for entry in bucket.values():
                if (not isinstance(entry, dict) or not isinstance(entry.get("flagging"), str)
                        or parse_date_value(entry.get("changed_at")) is None):
                    raise ValueError(f"Riwayat aging {group} tidak valid.")
            self.state[group] = bucket

    def save(self):
        """Save atomically; persistence errors must fail the job."""
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.state_path.with_suffix(f".tmp.{os.getpid()}")
        payload = {
            "updated_at": datetime.now(JAKARTA_TZ).isoformat(),
            "fo": self.state.get("fo", {}),
            "radio": self.state.get("radio", {}),
        }
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.state_path)

    def track_record(
        self,
        group: str,
        key: tuple[Any, ...],
        current_flagging: Any,
        is_2026: bool = True,
        existing_change_date: Any = None,
        order_date: Any = None,
    ) -> tuple[int | None, int | None, str]:
        """
        Track flagging change and calculate aging.

        Args:
            group: 'fo' or 'radio'
            key: tuple of identifying keys, e.g. (site_or_hop, sp, mitra)
            current_flagging: current progress status string
            is_2026: whether the record belongs to order 2026
            existing_change_date: change date already in the record (if any)
            order_date: date order was received / inhand date (fallback for cold start)

        Returns:
            (aging_days, change_date_serial, grouping_label)
            If not 2026 or flagging empty/invalid, returns (None, None, "").
        """
        if not is_2026:
            return None, None, ""

        flag_str = str(current_flagging or "").strip()
        # Clean error strings like #N/A, #REF!, etc.
        if not flag_str or flag_str.startswith(("#", "=")):
            return None, None, ""

        norm_flag = norm(flag_str)
        group_key = group.lower()
        key_str = "|".join(norm(part) for part in key)
        bucket = self.state.setdefault(group_key, {})
        entry = bucket.get(key_str)

        if entry is not None:
            prev_flag = norm(entry.get("flagging", ""))
            prev_date_str = entry.get("changed_at")
            prev_date = parse_date_value(prev_date_str) or self.today

            if norm_flag != prev_flag:
                # Flagging changed! Reset change date to today, aging = 0
                change_date = self.today
                aging_days = 0
                bucket[key_str] = {
                    "flagging": flag_str,
                    "changed_at": change_date.isoformat(),
                    "serial": to_excel_serial(change_date),
                }
            else:
                # Flagging unchanged: calculate days difference
                change_date = prev_date
                aging_days = max(0, (self.today - change_date).days)
        else:
            # Cold start (first time seeing this record)
            parsed_change = parse_date_value(existing_change_date)
            parsed_order = parse_date_value(order_date)

            if parsed_change is not None and parsed_change <= self.today:
                change_date = parsed_change
            elif parsed_order is not None and parsed_order <= self.today:
                change_date = parsed_order
            else:
                change_date = self.today

            aging_days = max(0, (self.today - change_date).days)
            bucket[key_str] = {
                "flagging": flag_str,
                "changed_at": change_date.isoformat(),
                "serial": to_excel_serial(change_date),
            }

        change_serial = to_excel_serial(change_date)
        grouping = (
            grouping_aging_fo(aging_days)
            if group_key == "fo"
            else grouping_aging_radio(aging_days)
        )
        return aging_days, change_serial, grouping
