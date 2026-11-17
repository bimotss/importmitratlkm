import fcntl
import json
import logging
import math
import os
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .history import HistoryStore, sanitize_error

LOG = logging.getLogger(__name__)


class BusyError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def exclusive_job(state_dir: Path):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    lock_file = state_dir / "import.lock"
    with lock_file.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusyError("Refresh lain sedang berjalan.") from exc
        try:
            os.chmod(lock_file, 0o644)
        except OSError:
            pass
        try:
            yield
        finally:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                pass


@contextmanager
def exclusive_validation(state_dir: Path):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    with (state_dir / "validation.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusyError("Pemeriksaan validasi mitra lain sedang berjalan.") from exc
        try:
            yield
        finally:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                pass


def file_busy(state_dir: Path) -> bool:
    try:
        with exclusive_job(state_dir):
            return False
    except BusyError:
        return True


def validation_busy(state_dir: Path) -> bool:
    try:
        with exclusive_validation(state_dir):
            return False
    except BusyError:
        return True


def next_deadline(deadline: float, now: float, interval: float) -> float:
    """Skip missed intervals instead of queuing overlapping hourly imports."""
    if deadline <= now:
        return deadline + (math.floor((now - deadline) / interval) + 1) * interval
    return deadline


class JobRunner:
    def __init__(self, settings, importer_factory, history_store=None):
        self.settings = settings
        self.importer_factory = importer_factory
        self.state_file = settings.state_dir / "status.json"
        self._state_lock = threading.Lock()

        if history_store is not None:
            self.history = history_store
        else:
            try:
                db_path = getattr(settings, "history_db_path", settings.state_dir / "history.db")
                retention = getattr(settings, "history_retention_days", 90)
                self.history = HistoryStore(
                    db_path,
                    retention_days=retention,
                    lock_checker=lambda: file_busy(settings.state_dir),
                )
            except Exception as h_init_err:  # noqa: BLE001
                LOG.error("Gagal menginisialisasi HistoryStore: %s. Import tetap berjalan tanpa history.", h_init_err)
                self.history = None

    def status(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (FileNotFoundError, ValueError):
            return {"status": "idle", "message": "Belum ada refresh dari VPS."}

    def _write_state(self, state: dict) -> None:
        """Tulis state status.json secara atomik dengan izin 0644."""
        with self._state_lock:
            self.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            temporary = self.state_file.with_suffix(f".{os.getpid()}_{secrets.token_hex(4)}.tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            try:
                temporary.chmod(0o644)
            except OSError:
                pass
            temporary.replace(self.state_file)

    def _update(self, **values) -> None:
        """Perbarui status.json untuk run aktif tanpa menghapus kunci penting run."""
        with self._state_lock:
            current = self.status()
            state = {**current, **values}
            self.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            temporary = self.state_file.with_suffix(f".{os.getpid()}_{secrets.token_hex(4)}.tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            try:
                temporary.chmod(0o644)
            except OSError:
                pass
            temporary.replace(self.state_file)

    def run(self, *, dry_run: bool = False, trigger: str = "unknown", job_type: str | None = None):
        # 1. Kunci eksklusif: jika proses lain sedang jalan, langsung raise BusyError
        # Sesuai aturan: Permintaan yang ditolak karena sibuk TIDAK dihitung sebagai eksekusi import!
        with exclusive_job(self.settings.state_dir):
            run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
            effective_job_type = job_type or ("dry_run" if dry_run else "normal")

            # 2. Catat awal eksekusi ke persistent HistoryStore
            if self.history:
                try:
                    self.history.start_run(
                        run_id=run_id,
                        job_type=effective_job_type,
                        trigger=trigger,
                        pid=os.getpid(),
                    )
                except Exception as h_start_err:  # noqa: BLE001
                    LOG.warning("Gagal mencatat start_run ke history: %s", h_start_err)

            # 3. Reset status.json untuk run baru ini
            # JANGAN MENYALIN duration_seconds, result, atau error dari run sebelumnya!
            old_status = self.status()
            initial_status = {
                "run_id": run_id,
                "job_type": effective_job_type,
                "trigger": trigger,
                "status": "running",
                "started_at": utc_now(),
                "finished_at": None,
                "error": None,
                "phase": "starting",
                "message": "Mulai refresh",
                "completed_sources": 0,
                "total_sources": None,
                "completed_tabs": 0,
                "total_tabs": None,
                "pid": os.getpid(),
                "history_health": self.history.health if self.history else "error",
                "history_warning": self.history.last_error if (self.history and self.history.last_error) else None,
            }
            # Pertahankan hanya last_success_at dari eksekusi sebelumnya (jika ada)
            if old_status.get("last_success_at"):
                initial_status["last_success_at"] = old_status["last_success_at"]

            self._write_state(initial_status)
            started = time.monotonic()
            last_progress = {"phase": "starting", "message": "Mulai refresh"}

            def progress_callback(**kwargs):
                last_progress.update({k: v for k, v in kwargs.items() if v is not None})
                self._update(**kwargs)
                if self.history:
                    try:
                        self.history.update_progress(
                            run_id=run_id,
                            phase=kwargs.get("phase"),
                            message=kwargs.get("message"),
                            completed_sources=kwargs.get("completed_sources"),
                            total_sources=kwargs.get("total_sources"),
                            completed_tabs=kwargs.get("completed_tabs"),
                            total_tabs=kwargs.get("total_tabs"),
                        )
                    except Exception as h_prog_err:  # noqa: BLE001
                        LOG.warning("Gagal update progress ke history: %s", h_prog_err)

            try:
                result = self.importer_factory().run(progress_callback, dry_run=dry_run)
                duration = round(time.monotonic() - started, 1)
                final_status = "dry_run_complete" if dry_run else "success"
                final_message = "Validasi selesai; tidak menulis data." if dry_run else "Refresh selesai."

                # Update status di HistoryStore
                if self.history:
                    try:
                        self.history.finish_run(
                            run_id=run_id,
                            status=final_status,
                            result=result,
                            duration_seconds=duration,
                        )
                    except Exception as h_fin_err:  # noqa: BLE001
                        LOG.warning("Gagal mencatat finish_run ke history: %s", h_fin_err)

                # Update status.json akhir
                values = {
                    "status": final_status,
                    "phase": "done",
                    "message": final_message,
                    "finished_at": utc_now(),
                    "duration_seconds": duration,
                    "result": result,
                    "history_health": self.history.health if self.history else "error",
                    "history_warning": self.history.last_error if (self.history and self.history.last_error) else None,
                }
                # Dry run tidak boleh memperbarui last_success_at!
                if not dry_run and effective_job_type != "dry_run":
                    values["last_success_at"] = utc_now()

                self._update(**values)
                return result
            except Exception as exc:
                duration = round(time.monotonic() - started, 1)
                safe_err = sanitize_error(str(exc))
                failed_phase = last_progress.get("phase", "failed")
                LOG.exception("Refresh gagal pada tahap %s", failed_phase)

                if self.history:
                    try:
                        self.history.finish_run(
                            run_id=run_id,
                            status="failed",
                            error=safe_err,
                            duration_seconds=duration,
                        )
                    except Exception as h_err_save:  # noqa: BLE001
                        LOG.warning("Gagal mencatat kegagalan ke history: %s", h_err_save)

                self._update(
                    status="failed",
                    phase=failed_phase,
                    error=safe_err,
                    message=f"Refresh gagal pada tahap {failed_phase}.",
                    finished_at=utc_now(),
                    duration_seconds=duration,
                    history_health=self.history.health if self.history else "error",
                    history_warning=self.history.last_error if (self.history and self.history.last_error) else None,
                )
                raise


class Worker:
    def __init__(self, runner, interval_seconds: int = 3600, refresh_on_start: bool = True):
        self.runner = runner
        self.interval = interval_seconds
        self.refresh_on_start = refresh_on_start
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._pending = False
        self._running = False
        self._trigger = "startup" if refresh_on_start else "scheduled"
        self.deadline = time.monotonic() + (0 if refresh_on_start else interval_seconds)
        self.thread = threading.Thread(target=self._loop, daemon=True, name="mitra-import")

    def start(self):
        self.thread.start()

    def close(self):
        self._stop.set()
        self._wake.set()
        self.thread.join(timeout=10)

    def request_refresh(self) -> bool:
        with self._lock:
            if self._pending or self._running or file_busy(self.runner.settings.state_dir):
                return False
            self._pending = True
            self._trigger = "dashboard"
            self._wake.set()
            return True

    def status(self) -> dict:
        with self._lock:
            active = self._pending or self._running
        active = active or file_busy(self.runner.settings.state_dir)
        state = self.runner.status()
        if state.get("status") == "running" and not active:
            state = {**state, "status": "interrupted", "message": "Proses terakhir terputus; coba refresh lagi."}
        return {
            **state,
            "running": active,
            "interval_seconds": self.interval,
            "next_run_at": datetime.fromtimestamp(
                time.time() + max(0, self.deadline - time.monotonic()),
                timezone.utc,
            ).isoformat(),
        }

    def _loop(self):
        is_first = True
        while not self._stop.is_set():
            self._wake.wait(timeout=max(0, self.deadline - time.monotonic()))
            self._wake.clear()
            if self._stop.is_set():
                return
            with self._lock:
                if not self._pending and time.monotonic() < self.deadline:
                    continue
                trigger = self._trigger if self._pending else ("startup" if (is_first and self.refresh_on_start) else "scheduled")
                self._pending, self._running = False, True
                self._trigger = "scheduled"  # reset untuk putaran selanjutnya
                is_first = False

            self.deadline = next_deadline(self.deadline, time.monotonic(), self.interval)
            try:
                try:
                    self.runner.run(trigger=trigger)
                except TypeError:
                    self.runner.run()
            except BusyError:
                LOG.info("Refresh dilewati: proses CLI lain sedang berjalan.")
            except Exception:
                LOG.exception("Job gagal; scheduler tetap berjalan untuk jadwal berikutnya.")
            finally:
                self.deadline = next_deadline(self.deadline, time.monotonic(), self.interval)
                with self._lock:
                    self._running = False
