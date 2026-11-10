import fcntl
import json
import logging
import math
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

LOG = logging.getLogger(__name__)


class BusyError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def exclusive_job(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_dir / "import.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusyError("Refresh lain sedang berjalan.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def file_busy(state_dir):
    try:
        with exclusive_job(state_dir):
            return False
    except BusyError:
        return True


def next_deadline(deadline, now, interval):
    """Skip missed intervals instead of queuing overlapping hourly imports."""
    if deadline <= now:
        return deadline + (math.floor((now - deadline) / interval) + 1) * interval
    return deadline


class JobRunner:
    def __init__(self, settings, importer_factory):
        self.settings, self.importer_factory = settings, importer_factory
        self.state_file = settings.state_dir / "status.json"
        self._state_lock = threading.Lock()

    def status(self):
        try:
            return json.loads(self.state_file.read_text())
        except (FileNotFoundError, ValueError):
            return {"status": "idle", "message": "Belum ada refresh dari VPS."}

    def _update(self, **values):
        with self._state_lock:
            state = {**self.status(), **values}
            self.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.state_file.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            temporary.chmod(0o600)
            temporary.replace(self.state_file)

    def run(self, *, dry_run=False):
        with exclusive_job(self.settings.state_dir):
            self._update(status="running", started_at=utc_now(), finished_at=None, error=None,
                         phase="starting", message="Mulai refresh", completed_sources=0, completed_tabs=0)
            started = time.monotonic()
            try:
                result = self.importer_factory().run(self._update, dry_run=dry_run)
                values = {"status": "dry_run_complete" if dry_run else "success", "phase": "done",
                          "message": "Validasi selesai; tidak menulis data." if dry_run else "Refresh selesai.",
                          "finished_at": utc_now(), "duration_seconds": round(time.monotonic() - started, 1),
                          "result": result}
                if not dry_run:
                    values["last_success_at"] = utc_now()
                self._update(**values)
                return result
            except Exception as exc:
                LOG.exception("Refresh gagal")
                self._update(status="failed", phase="failed", error=str(exc), message="Refresh gagal.",
                             finished_at=utc_now())
                raise


class Worker:
    def __init__(self, runner, interval_seconds=3600, refresh_on_start=True):
        self.runner, self.interval = runner, interval_seconds
        self.refresh_on_start = refresh_on_start
        self._lock = threading.Lock()
        self._wake, self._stop = threading.Event(), threading.Event()
        self._pending, self._running = False, False
        self.deadline = time.monotonic() + (0 if refresh_on_start else interval_seconds)
        self.thread = threading.Thread(target=self._loop, daemon=True, name="mitra-import")

    def start(self):
        self.thread.start()

    def close(self):
        self._stop.set()
        self._wake.set()
        self.thread.join(timeout=10)

    def request_refresh(self):
        with self._lock:
            if self._pending or self._running or file_busy(self.runner.settings.state_dir):
                return False
            self._pending = True
            self._wake.set()
            return True

    def status(self):
        with self._lock:
            active = self._pending or self._running
        active = active or file_busy(self.runner.settings.state_dir)
        state = self.runner.status()
        if state.get("status") == "running" and not active:
            state = {**state, "status": "interrupted", "message": "Proses terakhir terputus; coba refresh lagi."}
        return {**state, "running": active, "interval_seconds": self.interval,
                "next_run_at": datetime.fromtimestamp(time.time() + max(0, self.deadline - time.monotonic()),
                                                      timezone.utc).isoformat()}

    def _loop(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=max(0, self.deadline - time.monotonic()))
            self._wake.clear()
            if self._stop.is_set():
                return
            with self._lock:
                if not self._pending and time.monotonic() < self.deadline:
                    continue
                self._pending, self._running = False, True
            self.deadline = next_deadline(self.deadline, time.monotonic(), self.interval)
            try:
                self.runner.run()
            except BusyError:
                LOG.info("Refresh dilewati: proses CLI lain sedang berjalan.")
            except Exception:
                LOG.exception("Job gagal; scheduler tetap berjalan untuk jadwal berikutnya.")
            finally:
                self.deadline = next_deadline(self.deadline, time.monotonic(), self.interval)
                with self._lock:
                    self._running = False

