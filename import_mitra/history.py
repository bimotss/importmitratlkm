"""Penyimpanan Persisten Riwayat Eksekusi Impor Mitra (SQLite).

Fitur:
1. Pencatatan persisten eksekusi impor (normal, sync_cod, dry_run) dengan run_id unik.
2. Penanganan transaksi aman SQLite (WAL mode, busy_timeout 5 detik).
3. Pembaruan progres run yang sedang berjalan tanpa membuat baris baru.
4. Deteksi eksekusi terputus (interrupted) berdasarkan status proses/lock.
5. Pembersihan retensi otomatis (default 90 hari) tanpa menghapus run aktif.
6. Sanitasi pesan error agar tidak mengekspos rahasia, token, atau kredensial.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

# Daftar field sensitif untuk sanitasi
SENSITIVE_FIELDS = {
    "password": "[REDACTED_PASSWORD]",
    "client_secret": "[REDACTED_SECRET]",
    "secret": "[REDACTED_SECRET]",
    "private_key": "[REDACTED_KEY]",
    "access_token": "[REDACTED_TOKEN]",
    "refresh_token": "[REDACTED_TOKEN]",
    "token": "[REDACTED_TOKEN]",
    "api_key": "[REDACTED_TOKEN]",
    "apikey": "[REDACTED_TOKEN]",
    "key": "[REDACTED_KEY]",
    "authorization": "[REDACTED_AUTH]",
}

field_alt = "|".join(re.escape(f) for f in SENSITIVE_FIELDS)

RE_PRIV_KEY = re.compile(
    r"-----BEGIN (?:[A-Za-z0-9_-]+ )*PRIVATE KEY-----[\s\S]*?-----END (?:[A-Za-z0-9_-]+ )*PRIVATE KEY-----",
    re.IGNORECASE,
)
RE_JSON = re.compile(
    r'([\"\']?(' + field_alt + r')[\"\']?\s*[:=]\s*)([\"\'])(?:(?!\3).)*\3',
    re.IGNORECASE,
)
RE_AUTH = re.compile(
    r"\b(authorization\s*[:=]\s*)[^\r\n,;]+",
    re.IGNORECASE,
)
RE_BEARER = re.compile(
    r"\b(bearer\s+)[A-Za-z0-9_\-\.]{6,}",
    re.IGNORECASE,
)
RE_URL = re.compile(
    r"(?<=[?&])(" + field_alt + r")=([^\s&#]+)",
    re.IGNORECASE,
)
RE_KV = re.compile(
    r"\b(" + field_alt + r")\s*([:=])\s*(?!\[REDACTED)([^\s,;\"\'&]+)",
    re.IGNORECASE,
)


def _repl_json(m: re.Match) -> str:
    key = m.group(2).lower()
    tag = SENSITIVE_FIELDS.get(key, "[REDACTED]")
    return f"{m.group(1)}{m.group(3)}{tag}{m.group(3)}"


def _repl_url(m: re.Match) -> str:
    key = m.group(1).lower()
    tag = SENSITIVE_FIELDS.get(key, "[REDACTED]")
    return f"{m.group(1)}={tag}"


def _repl_kv(m: re.Match) -> str:
    key = m.group(1).lower()
    tag = SENSITIVE_FIELDS.get(key, "[REDACTED]")
    sep = m.group(2)
    spacing = " " if sep == ":" else ""
    return f"{m.group(1)}{sep}{spacing}{tag}"


def utc_now() -> str:
    """Waktu UTC ISO 8601 saat ini."""
    return datetime.now(timezone.utc).isoformat()


def sanitize_error(error_str: str | None) -> str | None:
    """Hapus token, password, atau credential dari pesan error sebelum disimpan."""
    if not error_str:
        return None
    clean = str(error_str)
    clean = RE_PRIV_KEY.sub("[REDACTED_PRIVATE_KEY]", clean)
    clean = RE_JSON.sub(_repl_json, clean)
    clean = RE_AUTH.sub(r"\g<1>[REDACTED_AUTH]", clean)
    clean = RE_BEARER.sub(r"\g<1>[REDACTED_TOKEN]", clean)
    clean = RE_URL.sub(_repl_url, clean)
    clean = RE_KV.sub(_repl_kv, clean)
    # Batasi panjang maksimal pesan error agar tidak membengkak
    if len(clean) > 2000:
        clean = clean[:2000] + "... [pesan dipotong]"
    return clean


def is_pid_alive(pid: int | None) -> bool:
    """Periksa apakah proses dengan PID tertentu masih hidup pada OS saat ini."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False  # ESRCH: proses sudah tidak ada
    except PermissionError:
        return True   # EPERM: proses ada namun milik user lain
    except (ValueError, OSError):
        return False


class HistoryStore:
    """Penyimpanan riwayat import mitra berbasis SQLite."""

    def __init__(
        self,
        db_path: Path | str,
        retention_days: int = 90,
        lock_checker: Any = None,
    ):
        self.db_path = Path(db_path).resolve()
        self.retention_days = max(1, retention_days)
        self.lock_checker = lock_checker
        self.health = "healthy"
        self.last_error: str | None = None
        self._wal_keeper: sqlite3.Connection | None = None
        self.init_db()
        if self.health == "healthy":
            try:
                # Keep WAL/SHM present so a different read-only OS user never
                # needs write permission on the report directory to recreate them.
                self._wal_keeper = sqlite3.connect(str(self.db_path), check_same_thread=False)
                self._wal_keeper.execute("SELECT count(*) FROM import_runs").fetchone()
                self._ensure_file_permissions()
            except (OSError, sqlite3.Error) as exc:
                self.health = "error"
                self.last_error = str(exc)
                LOG.error("Gagal mempertahankan akses reader WAL history: %s", exc)

    def close(self) -> None:
        """Release the persistent WAL reader connection on shutdown."""
        if self._wal_keeper is not None:
            self._wal_keeper.close()
            self._wal_keeper = None


    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Koneksi SQLite dengan WAL mode dan timeout."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=5.0,
                isolation_level=None,  # Autocommit, gunakan transaksi eksplisit
            )
        except (OSError, sqlite3.Error) as exc:
            self.health = "error"
            self.last_error = str(exc)
            raise sqlite3.Error(f"Gagal membuka database history di {self.db_path}: {exc}") from exc

        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            yield conn
        finally:
            conn.close()

    def _ensure_file_permissions(self) -> None:
        """Pastikan file database dapat dibaca oleh service lain (0644)."""
        try:
            if self.db_path.exists():
                self.db_path.chmod(0o644)
            wal_file = self.db_path.with_name(f"{self.db_path.name}-wal")
            if wal_file.exists():
                wal_file.chmod(0o644)
            shm_file = self.db_path.with_name(f"{self.db_path.name}-shm")
            if shm_file.exists():
                shm_file.chmod(0o644)
        except OSError:
            pass

    def init_db(self) -> None:
        """Inisialisasi tabel dan indeks riwayat eksekusi."""
        try:
            with self._conn() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS import_runs (
                            run_id TEXT PRIMARY KEY,
                            job_type TEXT NOT NULL,
                            trigger TEXT NOT NULL,
                            started_at TEXT NOT NULL,
                            finished_at TEXT,
                            status TEXT NOT NULL,
                            phase TEXT,
                            message TEXT,
                            completed_sources INTEGER,
                            total_sources INTEGER,
                            completed_tabs INTEGER,
                            total_tabs INTEGER,
                            duration_seconds REAL,
                            fo_rows INTEGER,
                            radio_rows INTEGER,
                            total_rows INTEGER,
                            sources_count INTEGER,
                            tabs_count INTEGER,
                            cod_summary TEXT,
                            error TEXT,
                            pid INTEGER,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            last_phase TEXT,
                            last_message TEXT
                        );
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_import_runs_started_at
                        ON import_runs (started_at DESC);
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_import_runs_status
                        ON import_runs (status);
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_import_runs_job_type
                        ON import_runs (job_type);
                    """)

                    # Safe migration untuk database existing
                    table_info = conn.execute("PRAGMA table_info(import_runs)").fetchall()
                    existing_cols = {r["name"] for r in table_info}
                    if "last_phase" not in existing_cols:
                        conn.execute("ALTER TABLE import_runs ADD COLUMN last_phase TEXT;")
                    if "last_message" not in existing_cols:
                        conn.execute("ALTER TABLE import_runs ADD COLUMN last_message TEXT;")

                    conn.execute("COMMIT;")
                    self.health = "healthy"
                    self.last_error = None
                except (sqlite3.Error, OSError):
                    conn.execute("ROLLBACK;")
                    raise
            self._ensure_file_permissions()
        except (sqlite3.Error, OSError) as exc:
            self.health = "error"
            self.last_error = str(exc)
            LOG.warning("Penyimpanan history gagal diinisialisasi pada %s: %s", self.db_path, exc)

    def start_run(
        self,
        run_id: str,
        job_type: str = "normal",
        trigger: str = "unknown",
        pid: int | None = None,
    ) -> dict[str, Any] | None:
        """Catat awal eksekusi import baru dengan status 'running'."""
        now = utc_now()
        effective_pid = pid or os.getpid()

        # Rekonsiliasi run lama yang mungkin terputus sebelum memulai run baru
        try:
            self.reconcile_interrupted_runs(exclude_run_id=run_id)
        except (sqlite3.Error, OSError) as exc:
            LOG.warning("Gagal merekonsiliasi run terputus sebelum start: %s", exc)

        # Pembersihan retensi (jangan menghapus run aktif)
        try:
            self.cleanup_retention()
        except (sqlite3.Error, OSError) as exc:
            LOG.warning("Gagal membersihkan retensi history: %s", exc)

        record = {
            "run_id": run_id,
            "job_type": job_type,
            "trigger": trigger,
            "started_at": now,
            "finished_at": None,
            "status": "running",
            "phase": "starting",
            "message": "Mulai eksekusi import",
            "last_phase": "starting",
            "last_message": "Mulai eksekusi import",
            "completed_sources": 0,
            "total_sources": None,
            "completed_tabs": 0,
            "total_tabs": None,
            "duration_seconds": None,
            "fo_rows": None,
            "radio_rows": None,
            "total_rows": None,
            "sources_count": None,
            "tabs_count": None,
            "cod_summary": None,
            "error": None,
            "pid": effective_pid,
            "created_at": now,
            "updated_at": now,
        }

        try:
            with self._conn() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    conn.execute(
                        """
                        INSERT INTO import_runs (
                            run_id, job_type, trigger, started_at, finished_at,
                            status, phase, message, last_phase, last_message,
                            completed_sources, total_sources, completed_tabs, total_tabs,
                            duration_seconds, fo_rows, radio_rows, total_rows,
                            sources_count, tabs_count, cod_summary, error, pid,
                            created_at, updated_at
                        ) VALUES (
                            :run_id, :job_type, :trigger, :started_at, :finished_at,
                            :status, :phase, :message, :last_phase, :last_message,
                            :completed_sources, :total_sources, :completed_tabs, :total_tabs,
                            :duration_seconds, :fo_rows, :radio_rows, :total_rows,
                            :sources_count, :tabs_count, :cod_summary, :error, :pid,
                            :created_at, :updated_at
                        )
                        ON CONFLICT(run_id) DO UPDATE SET
                            updated_at = :updated_at
                        """,
                        record,
                    )
                    conn.execute("COMMIT;")
                    self.health = "healthy"
                except (sqlite3.Error, OSError):
                    conn.execute("ROLLBACK;")
                    raise
            self._ensure_file_permissions()
            return record
        except (sqlite3.Error, OSError) as exc:
            self.health = "error"
            self.last_error = str(exc)
            LOG.error("Gagal mencatat start_run %s ke database history: %s", run_id, exc)
            return None

    def update_progress(
        self,
        run_id: str,
        phase: str | None = None,
        message: str | None = None,
        completed_sources: int | None = None,
        total_sources: int | None = None,
        completed_tabs: int | None = None,
        total_tabs: int | None = None,
    ) -> None:
        """Pembaruan progres berkala pada run yang sama."""
        now = utc_now()
        try:
            with self._conn() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    conn.execute(
                        """
                        UPDATE import_runs
                        SET phase = COALESCE(:phase, phase),
                            message = COALESCE(:message, message),
                            last_phase = COALESCE(:phase, last_phase, phase),
                            last_message = COALESCE(:message, last_message, message),
                            completed_sources = COALESCE(:completed_sources, completed_sources),
                            total_sources = COALESCE(:total_sources, total_sources),
                            completed_tabs = COALESCE(:completed_tabs, completed_tabs),
                            total_tabs = COALESCE(:total_tabs, total_tabs),
                            updated_at = :updated_at
                        WHERE run_id = :run_id AND status = 'running'
                        """,
                        {
                            "run_id": run_id,
                            "phase": phase,
                            "message": message,
                            "completed_sources": completed_sources,
                            "total_sources": total_sources,
                            "completed_tabs": completed_tabs,
                            "total_tabs": total_tabs,
                            "updated_at": now,
                        },
                    )
                    conn.execute("COMMIT;")
                    self.health = "healthy"
                except (sqlite3.Error, OSError):
                    conn.execute("ROLLBACK;")
                    raise
        except (sqlite3.Error, OSError) as exc:
            self.health = "error"
            self.last_error = str(exc)
            LOG.warning("Gagal memperbarui progres run %s di history: %s", run_id, exc)

    def finish_run(
        self,
        run_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Catat penyelesaian run (success, dry_run_complete, failed, atau interrupted)."""
        now = utc_now()
        safe_error = sanitize_error(error)

        fo_rows = None
        radio_rows = None
        total_rows = None
        sources_count = None
        tabs_count = None
        cod_summary_json = None

        if result and isinstance(result, dict):
            fo_rows = result.get("fo")
            radio_rows = result.get("radio")
            total_rows = result.get("total")
            sources_count = result.get("sources")
            tabs_count = result.get("tabs")
            if result.get("cod"):
                try:
                    cod_summary_json = json.dumps(result["cod"], ensure_ascii=False)
                except (TypeError, ValueError):
                    cod_summary_json = None

        # Jika duration_seconds belum dihitung, hitung berdasarkan started_at (hanya untuk run selesai normal/gagal)
        calc_duration = duration_seconds
        finished_at_val: str | None = now
        if status == "interrupted":
            # Jangan mengarang waktu selesai atau durasi pasti untuk proses crash
            finished_at_val = None
            calc_duration = None
        elif calc_duration is None:
            current = self.get_run(run_id)
            if current and current.get("started_at"):
                try:
                    s_dt = datetime.fromisoformat(current["started_at"])
                    f_dt = datetime.fromisoformat(now)
                    calc_duration = round((f_dt - s_dt).total_seconds(), 1)
                except (ValueError, TypeError):
                    calc_duration = None

        if status in ("success", "dry_run_complete"):
            final_phase = "done"
            default_msg = "Refresh selesai." if status == "success" else "Validasi selesai (dry run)."
        else:
            final_phase = None
            default_msg = None

        try:
            with self._conn() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    conn.execute(
                        """
                        UPDATE import_runs
                        SET status = :status,
                            phase = CASE
                                WHEN :final_phase IS NOT NULL THEN :final_phase
                                ELSE COALESCE(phase, last_phase, 'unknown')
                            END,
                            message = CASE
                                WHEN :default_msg IS NOT NULL THEN :default_msg
                                ELSE COALESCE(message, last_message, 'Selesai dengan kesalahan.')
                            END,
                            last_phase = COALESCE(last_phase, phase),
                            last_message = COALESCE(last_message, message),
                            finished_at = :finished_at,
                            duration_seconds = COALESCE(:duration_seconds, duration_seconds),
                            fo_rows = COALESCE(:fo_rows, fo_rows),
                            radio_rows = COALESCE(:radio_rows, radio_rows),
                            total_rows = COALESCE(:total_rows, total_rows),
                            sources_count = COALESCE(:sources_count, sources_count),
                            tabs_count = COALESCE(:tabs_count, tabs_count),
                            cod_summary = COALESCE(:cod_summary, cod_summary),
                            error = :error,
                            updated_at = :updated_at
                        WHERE run_id = :run_id
                        """,
                        {
                            "run_id": run_id,
                            "status": status,
                            "final_phase": final_phase,
                            "default_msg": default_msg,
                            "finished_at": finished_at_val,
                            "duration_seconds": calc_duration,
                            "fo_rows": fo_rows,
                            "radio_rows": radio_rows,
                            "total_rows": total_rows,
                            "sources_count": sources_count,
                            "tabs_count": tabs_count,
                            "cod_summary": cod_summary_json,
                            "error": safe_error,
                            "updated_at": now,
                        },
                    )
                    conn.execute("COMMIT;")
                    self.health = "healthy"
                except (sqlite3.Error, OSError):
                    conn.execute("ROLLBACK;")
                    raise
            self._ensure_file_permissions()
        except (sqlite3.Error, OSError) as exc:
            self.health = "error"
            self.last_error = str(exc)
            LOG.error("Gagal mencatat finish_run %s di history: %s", run_id, exc)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Ambil rincian satu eksekusi import berdasarkan run_id."""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM import_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row:
                    return dict(row)
        except sqlite3.Error as exc:
            LOG.error("Gagal membaca run %s: %s", run_id, exc)
        return None

    def list_runs(
        self,
        page: int = 1,
        per_page: int = 25,
        status: str | None = None,
        job_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Daftar riwayat import berurutan dari yang terbaru dengan pagination dan filter.
        
        Returns:
            (runs, total_filtered_count)
        """
        page = max(1, page)
        per_page = max(5, min(100, per_page))
        offset = (page - 1) * per_page

        conditions: list[str] = []
        params: dict[str, Any] = {}

        if status and status != "all":
            conditions.append("status = :status")
            params["status"] = status

        if job_type and job_type != "all":
            conditions.append("job_type = :job_type")
            params["job_type"] = job_type

        if date_from:
            conditions.append("started_at >= :date_from")
            params["date_from"] = date_from

        if date_to:
            conditions.append("started_at <= :date_to")
            params["date_to"] = date_to

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        try:
            with self._conn() as conn:
                count_row = conn.execute(
                    f"SELECT COUNT(*) as total FROM import_runs {where_clause}",
                    params,
                ).fetchone()
                total = count_row["total"] if count_row else 0

                query = f"""
                    SELECT * FROM import_runs
                    {where_clause}
                    ORDER BY started_at DESC
                    LIMIT :limit OFFSET :offset
                """
                rows = conn.execute(
                    query,
                    {**params, "limit": per_page, "offset": offset},
                ).fetchall()
                return [dict(r) for r in rows], total
        except sqlite3.Error as exc:
            LOG.error("Gagal mengambil daftar runs dari history: %s", exc)
            return [], 0

    def get_last_successful_import(self) -> dict[str, Any] | None:
        """Ambil eksekusi import normal terakhir yang berstatus success."""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM import_runs
                    WHERE status = 'success' AND job_type = 'normal'
                    ORDER BY finished_at DESC, started_at DESC
                    LIMIT 1
                    """
                ).fetchone()
                if row:
                    return dict(row)
        except sqlite3.Error as exc:
            LOG.error("Gagal mengambil last successful import: %s", exc)
        return None

    def get_active_run(self) -> dict[str, Any] | None:
        """Ambil run yang saat ini berstatus 'running' (jika masih aktif)."""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM import_runs
                    WHERE status = 'running'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None

                data = dict(row)
                # Validasi apakah benar-benar masih aktif
                is_lock_held = self.lock_checker() if callable(self.lock_checker) else True
                is_proc_alive = is_pid_alive(data.get("pid"))

                if not is_lock_held or not is_proc_alive:
                    # Proses sudah mati atau lock sudah lepas -> tandai interrupted
                    self.finish_run(
                        data["run_id"],
                        status="interrupted",
                        error="Proses berhenti tanpa menyelesaikan tahap akhir (terputus).",
                    )
                    return None

                return data
        except sqlite3.Error as exc:
            LOG.error("Gagal memeriksa active run: %s", exc)
            return None

    def reconcile_interrupted_runs(self, exclude_run_id: str | None = None) -> int:
        """Deteksi dan perbarui run running yang prosesnya sudah mati atau lock-nya lepas."""
        updated = 0
        try:
            with self._conn() as conn:
                query = "SELECT * FROM import_runs WHERE status = 'running'"
                params: list[Any] = []
                if exclude_run_id:
                    query += " AND run_id != ?"
                    params.append(exclude_run_id)

                running_rows = conn.execute(query, params).fetchall()
                for r in running_rows:
                    pid = r["pid"]
                    is_lock_held = self.lock_checker() if callable(self.lock_checker) else False
                    is_proc_alive = is_pid_alive(pid)

                    # Jika lock lepas atau pid sudah mati, maka terputus
                    if not is_lock_held or not is_proc_alive:
                        conn.execute(
                            """
                            UPDATE import_runs
                            SET status = 'interrupted',
                                last_phase = COALESCE(last_phase, phase),
                                last_message = COALESCE(last_message, message),
                                phase = COALESCE(phase, last_phase, 'unknown'),
                                message = 'Proses terputus sebelum selesai (restart/crash).',
                                finished_at = NULL,
                                duration_seconds = NULL,
                                error = COALESCE(error, 'Proses terputus sebelum selesai (restart/crash).'),
                                updated_at = :now
                            WHERE run_id = :run_id
                            """,
                            {"run_id": r["run_id"], "now": utc_now()},
                        )
                        updated += 1
                        LOG.info("Run %s ditandai interrupted (tahap terakhir: %s).", r["run_id"], r["phase"])
        except (sqlite3.Error, OSError) as exc:
            self.health = "error"
            self.last_error = str(exc)
            LOG.warning("Gagal rekonsiliasi interrupted runs: %s", exc)
        return updated

    def cleanup_retention(self) -> int:
        """Hapus riwayat eksekusi yang sudah selesai dan melewati batas retensi hari."""
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        cutoff_iso = cutoff_dt.isoformat()
        deleted = 0
        try:
            with self._conn() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    # JANGAN PERNAH menghapus run yang berstatus running!
                    cursor = conn.execute(
                        """
                        DELETE FROM import_runs
                        WHERE status != 'running' AND started_at < ?
                        """,
                        (cutoff_iso,),
                    )
                    deleted = cursor.rowcount
                    conn.execute("COMMIT;")
                    if deleted > 0:
                        LOG.info("Membersihkan %s riwayat import lama (> %s hari).", deleted, self.retention_days)
                except sqlite3.Error:
                    conn.execute("ROLLBACK;")
                    raise
        except sqlite3.Error as exc:
            LOG.warning("Gagal pembersihan retensi history: %s", exc)
        return deleted
