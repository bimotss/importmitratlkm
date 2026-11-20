"""Consume one fixed upload queue as the importer user, sharing its job lock."""
import fcntl
import json
import os
import time

from .jobs import BusyError, JobRunner
from .jpp import JPPImport, write_status


def process_upload(settings):
    folder = settings.jpp_upload_dir
    descriptor = os.open(folder / "queue.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o660)
    with os.fdopen(descriptor, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = folder / "pending"
        if not marker.exists():
            return
        metadata = json.loads(marker.read_text())
        filename = str(metadata.get("filename", "JPP.xlsx"))[:200]
        upload = folder / "upload.xlsx"
        try:
            if upload.is_symlink() or not upload.is_file():
                raise ValueError("File upload tidak tersedia.")
            write_status(folder, status="waiting", filename=filename,
                         message="Menunggu impor aktif selesai sebelum update JPP.")
            deadline = time.monotonic() + 1800

            def make_importer():
                write_status(folder, status="running", filename=filename, message="Update JPP sedang berjalan.")
                return JPPImport(settings, upload)

            runner = JobRunner(settings, make_importer)
            while True:
                try:
                    result = runner.run(trigger="web", job_type="update_jpp")
                    break
                except BusyError:
                    if time.monotonic() >= deadline:
                        raise ValueError("Impor lain masih aktif setelah 30 menit; silakan upload ulang.")
                    time.sleep(5)
            write_status(folder, status="success", filename=filename,
                         message=f"JPP berhasil diperbarui: {result['total']:,} baris. Header dan posisi kolom tetap.",
                         total=result["total"], missing_columns=result["missing_columns"])
        except Exception as exc:
            from .history import sanitize_error
            write_status(folder, status="failed", filename=filename, message=sanitize_error(str(exc)))
            raise
        finally:
            marker.unlink(missing_ok=True)
            upload.unlink(missing_ok=True)
