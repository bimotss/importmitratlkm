"""Unit tests untuk HistoryStore dan pencatatan riwayat eksekusi di ImportMitra."""

import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from import_mitra.history import HistoryStore, is_pid_alive, sanitize_error
from import_mitra.jobs import BusyError, JobRunner, exclusive_job


def test_sanitize_error_removes_secrets():
    raw_error = (
        "Failed with Bearer ya29.a0AfH6SM... and client_secret: supersecret123 "
        "and password=my_db_password and token=secret_oauth_token"
    )
    cleaned = sanitize_error(raw_error)
    assert "supersecret123" not in cleaned
    assert "my_db_password" not in cleaned
    assert "secret_oauth_token" not in cleaned
    assert "ya29.a0AfH6SM" not in cleaned
    assert "[REDACTED_SECRET]" in cleaned
    assert "[REDACTED_PASSWORD]" in cleaned


def test_is_pid_alive():
    current_pid = os.getpid()
    assert is_pid_alive(current_pid) is True
    # Non-existent PID
    assert is_pid_alive(9999999) is False
    assert is_pid_alive(None) is False
    assert is_pid_alive(-1) is False


def test_start_run_and_update_progress(tmp_path):
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)

    run = store.start_run(run_id="run_001", job_type="normal", trigger="scheduled", pid=os.getpid())
    assert run["run_id"] == "run_001"
    assert run["status"] == "running"
    assert run["job_type"] == "normal"
    assert run["trigger"] == "scheduled"
    assert run["finished_at"] is None

    # Update progress
    store.update_progress(
        run_id="run_001",
        phase="reading",
        message="Membaca sumber 5/34",
        completed_sources=5,
        total_sources=34,
    )

    updated = store.get_run("run_001")
    assert updated["phase"] == "reading"
    assert updated["completed_sources"] == 5
    assert updated["total_sources"] == 34
    assert updated["status"] == "running"  # Status tetap running saat progress update


def test_finish_run_success(tmp_path):
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)

    store.start_run("run_002", job_type="normal", trigger="cli", pid=os.getpid())
    result = {
        "fo": 6900,
        "radio": 1700,
        "total": 8600,
        "sources": 34,
        "tabs": 3,
        "cod": {"total": 8600, "preserve_columns": True},
    }
    store.finish_run("run_002", status="success", result=result, duration_seconds=12.5)

    finished = store.get_run("run_002")
    assert finished["status"] == "success"
    assert finished["duration_seconds"] == 12.5
    assert finished["fo_rows"] == 6900
    assert finished["radio_rows"] == 1700
    assert finished["total_rows"] == 8600
    assert finished["sources_count"] == 34
    assert finished["tabs_count"] == 3
    assert finished["finished_at"] is not None
    assert "8600" in finished["cod_summary"]


def test_finish_run_failed(tmp_path):
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)

    store.start_run("run_003", job_type="normal", trigger="scheduled", pid=os.getpid())
    store.update_progress("run_003", phase="staging", message="Staging error")
    store.finish_run("run_003", status="failed", error="Google API 503 Backend Error with Bearer xyz12345678", duration_seconds=5.0)

    failed = store.get_run("run_003")
    assert failed["status"] == "failed"
    assert failed["phase"] == "staging"  # Tahap terakhir saat gagal dipertahankan!
    assert "xyz12345678" not in failed["error"]
    assert "[REDACTED_TOKEN]" in failed["error"]
    assert failed["duration_seconds"] == 5.0


def test_interrupted_run_detection_after_restart(tmp_path):
    """Run berstatus running yang prosesnya sudah mati atau lock lepas dikenali sebagai interrupted."""
    db_path = tmp_path / "history.db"

    # Lock checker returns False (artinya lock sudah tidak dipegang)
    store = HistoryStore(db_path, lock_checker=lambda: False)

    # Buat run dengan PID mati (misal 9999999)
    store.start_run("run_interrupted", job_type="normal", trigger="startup", pid=9999999)

    # Reconcile
    count = store.reconcile_interrupted_runs()
    assert count == 1

    r = store.get_run("run_interrupted")
    assert r["status"] == "interrupted"
    assert "terputus" in r["message"]


def test_running_cli_is_not_marked_interrupted_when_alive_and_locked(tmp_path):
    """Proses CLI yang masih berjalan dan memegang lock tidak boleh ditandai interrupted."""
    db_path = tmp_path / "history.db"

    # Lock checker returns True (lock sedang dipegang)
    store = HistoryStore(db_path, lock_checker=lambda: True)

    # Masukkan run dengan current PID (masih hidup!)
    store.start_run("run_live_cli", job_type="normal", trigger="cli", pid=os.getpid())

    # Reconcile harus mengabaikan run aktif yang hidup
    count = store.reconcile_interrupted_runs()
    assert count == 0

    active = store.get_active_run()
    assert active is not None
    assert active["run_id"] == "run_live_cli"
    assert active["status"] == "running"


def test_busy_request_does_not_create_new_run(tmp_path):
    """Permintaan yang ditolak karena busy TIDAK boleh membuat run baru di history."""
    state_dir = tmp_path / "state"
    settings = SimpleNamespace(state_dir=state_dir, history_db_path=state_dir / "history.db", history_retention_days=90)
    history_store = HistoryStore(settings.history_db_path)

    # Pegang lock secara manual
    with exclusive_job(state_dir):
        runner = JobRunner(settings, lambda: None, history_store=history_store)
        with pytest.raises(BusyError):
            runner.run()

    runs, total = history_store.list_runs()
    assert total == 0
    assert len(runs) == 0


def test_new_run_resets_previous_duration_and_result(tmp_path):
    """Run baru tidak boleh mewarisi duration_seconds atau result dari run sebelumnya."""
    state_dir = tmp_path / "state"
    settings = SimpleNamespace(state_dir=state_dir, history_db_path=state_dir / "history.db", history_retention_days=90)
    history_store = HistoryStore(settings.history_db_path)

    # Fake importer yang mengembalikan hasil pada run 1
    importer_mock = MagicMock()
    importer_mock.run.return_value = {"fo": 100, "radio": 20, "total": 120}

    runner = JobRunner(settings, lambda: importer_mock, history_store=history_store)

    # Run 1: Sukses
    res1 = runner.run()
    assert res1["total"] == 120
    status1 = runner.status()
    assert status1["status"] == "success"
    assert status1["duration_seconds"] is not None
    assert status1["result"]["total"] == 120

    # Sekarang simulasikan Run 2 yang baru mulai (kita periksa state saat running)
    importer_mock2 = MagicMock()

    def run2_impl(progress, dry_run=False):
        # Saat di tengah eksekusi, periksa status.json
        mid_status = runner.status()
        assert mid_status["status"] == "running"
        # duration_seconds dan result dari run 1 HARUS sudah bersih!
        assert "duration_seconds" not in mid_status or mid_status.get("duration_seconds") is None
        assert "result" not in mid_status or mid_status.get("result") is None
        return {"fo": 200, "radio": 40, "total": 240}

    importer_mock2.run.side_effect = run2_impl
    runner2 = JobRunner(settings, lambda: importer_mock2, history_store=history_store)
    runner2.run()

    # Periksa history mencatat 2 run terpisah dengan run_id berbeda
    runs, total = history_store.list_runs()
    assert total == 2
    assert runs[0]["run_id"] != runs[1]["run_id"]


def test_dry_run_and_sync_cod_distinguished(tmp_path):
    """Dry run dan sync-cod dibedakan dari import normal."""
    state_dir = tmp_path / "state"
    settings = SimpleNamespace(state_dir=state_dir, history_db_path=state_dir / "history.db", history_retention_days=90)
    history_store = HistoryStore(settings.history_db_path)

    importer_mock = MagicMock()
    importer_mock.run.return_value = {"fo": 50, "radio": 10, "total": 60}

    runner = JobRunner(settings, lambda: importer_mock, history_store=history_store)

    # 1. Normal run
    runner.run(trigger="scheduled", job_type="normal")

    # 2. Dry run
    runner.run(dry_run=True, trigger="cli", job_type="dry_run")

    # 3. Sync COD
    runner.run(trigger="cli", job_type="sync_cod")

    runs, total = history_store.list_runs()
    assert total == 3
    types = [r["job_type"] for r in runs]
    assert "sync_cod" in types
    assert "dry_run" in types
    assert "normal" in types


def test_retention_does_not_delete_active_running_run(tmp_path):
    """Pembersihan retensi lama tidak boleh menghapus run yang berstatus running."""
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path, retention_days=1)

    # Buat run lama yang sudah selesai (> 2 hari lalu)
    old_time = "2020-01-01T00:00:00+00:00"
    with store._conn() as conn:
        conn.execute(
            """
            INSERT INTO import_runs (run_id, job_type, trigger, started_at, finished_at, status, created_at, updated_at)
            VALUES ('old_finished', 'normal', 'scheduled', ?, ?, 'success', ?, ?)
            """,
            (old_time, old_time, old_time, old_time),
        )
        # Buat run lama yang MASIH running (misal proses panjang/terekam lama)
        conn.execute(
            """
            INSERT INTO import_runs (run_id, job_type, trigger, started_at, status, created_at, updated_at)
            VALUES ('old_running', 'normal', 'scheduled', ?, 'running', ?, ?)
            """,
            (old_time, old_time, old_time),
        )

    deleted = store.cleanup_retention()
    assert deleted == 1  # Hanya old_finished yang terhapus

    assert store.get_run("old_finished") is None
    assert store.get_run("old_running") is not None  # Active run tetap aman!


def test_list_runs_pagination_and_filters(tmp_path):
    """Uji pagination dan filter status serta job_type."""
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)

    for i in range(15):
        jtype = "normal" if i % 2 == 0 else "dry_run"
        status = "success" if i % 3 != 0 else "failed"
        run_id = f"run_{i:02d}"
        store.start_run(run_id, job_type=jtype, trigger="cli")
        store.finish_run(run_id, status=status)

    # Page 1 (per_page=5)
    p1, total = store.list_runs(page=1, per_page=5)
    assert total == 15
    assert len(p1) == 5

    # Filter status=failed
    failed_runs, f_total = store.list_runs(status="failed")
    assert f_total == 5
    assert all(r["status"] == "failed" for r in failed_runs)

    # Filter job_type=dry_run
    dry_runs, d_total = store.list_runs(job_type="dry_run")
    assert d_total == 7
    assert all(r["job_type"] == "dry_run" for r in dry_runs)


# ==============================================================================
# REGRESSION TESTS (Review Findings 2, 3, 5)
# ==============================================================================


def test_sanitize_error_comprehensive():
    """Temuan 3: Sanitasi error harus menangani JSON, private key, URL, auth headers, dan key=value."""
    # 1. JSON format
    json_err = 'Error response: {"access_token": "SYNTHETIC_SECRET_TOKEN", "client_secret": "SYNTHETIC_SECRET_KEY"}'
    c1 = sanitize_error(json_err)
    assert "SYNTHETIC_SECRET_TOKEN" not in c1
    assert "SYNTHETIC_SECRET_KEY" not in c1
    assert '{"access_token": "[REDACTED_TOKEN]"' in c1
    assert '"client_secret": "[REDACTED_SECRET]"' in c1

    # 2. Private Key Block (bare and with prefix)
    pk_bare = (
        "Failed loading credentials:\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC7SYNTHETIC_KEY\n"
        "-----END PRIVATE KEY-----\n"
        "caused by bad format"
    )
    c2 = sanitize_error(pk_bare)
    assert "SYNTHETIC_KEY" not in c2
    assert "[REDACTED_PRIVATE_KEY]" in c2
    assert "caused by bad format" in c2

    pk_rsa = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0SYNTHETIC_RSA_KEY\n"
        "-----END RSA PRIVATE KEY-----"
    )
    c3 = sanitize_error(pk_rsa)
    assert "SYNTHETIC_RSA_KEY" not in c3
    assert "[REDACTED_PRIVATE_KEY]" in c3

    # 3. Authorization Header & Bearer
    auth_err = "HTTP 401: Authorization: Bearer ya29.SYNTHETIC_BEARER_TOKEN"
    c4 = sanitize_error(auth_err)
    assert "SYNTHETIC_BEARER_TOKEN" not in c4
    assert "[REDACTED_AUTH]" in c4

    bearer_err = "failed with Bearer ya29.SYNTHETIC_BEARER_TOKEN"
    c_b = sanitize_error(bearer_err)
    assert "SYNTHETIC_BEARER_TOKEN" not in c_b
    assert "[REDACTED_TOKEN]" in c_b

    auth_basic = "HTTP 401: Authorization: Basic dXNlcjpwYXNzd29yZA=="
    c5 = sanitize_error(auth_basic)
    assert "dXNlcjpwYXNzd29yZA==" not in c5
    assert "[REDACTED_AUTH]" in c5

    # 4. URL query parameters
    url_err = "Failed request to https://sheets.googleapis.com/v4/spreadsheets?access_token=SYNTHETIC_URL_TOKEN&key=SYNTHETIC_API_KEY&id=123"
    c6 = sanitize_error(url_err)
    assert "SYNTHETIC_URL_TOKEN" not in c6
    assert "SYNTHETIC_API_KEY" not in c6
    assert "access_token=[REDACTED_TOKEN]" in c6
    assert "key=[REDACTED_KEY]" in c6
    assert "id=123" in c6  # Non-sensitive query preserved

    # 5. Key-Value pairs
    kv_err = "Connection failed for user=admin password=SYNTHETIC_PASSWORD client_secret: SYNTHETIC_SECRET"
    c7 = sanitize_error(kv_err)
    assert "SYNTHETIC_PASSWORD" not in c7
    assert "SYNTHETIC_SECRET" not in c7
    assert "password=[REDACTED_PASSWORD]" in c7
    assert "client_secret: [REDACTED_SECRET]" in c7

    # 6. Normal diagnostic message remains completely readable
    norm_err = "Sheet 'Data' tidak ditemukan pada spreadsheet 1sOCXDmi2uliL7LCUjd-XjMq"
    c8 = sanitize_error(norm_err)
    assert c8 == norm_err


def test_storage_parent_is_file_does_not_crash(tmp_path):
    """Temuan 2: Parent path berupa file tidak boleh melempar FileExistsError dan menghentikan runner."""
    # Buat file biasa sebagai parent
    fake_parent = tmp_path / "regular_file.txt"
    fake_parent.write_text("i am a file")

    bad_db_path = fake_parent / "history.db"

    # Inisialisasi HistoryStore tidak boleh melempar exception keluar
    store = HistoryStore(bad_db_path)
    assert store.health == "error"
    assert store.last_error is not None

    # Operasi store tidak crash
    assert store.start_run("run_err") is None
    store.update_progress("run_err", phase="fetch")
    store.finish_run("run_err", status="failed")

    # Inisialisasi JobRunner dengan db_path tersebut tetap berjalan
    settings = SimpleNamespace(
        state_dir=tmp_path / "state",
        history_db_path=bad_db_path,
        history_retention_days=90,
    )
    fake_importer = MagicMock()
    fake_importer.run.return_value = {"total": 100, "fo": 50, "radio": 50}
    runner = JobRunner(settings, lambda: fake_importer)

    # Runner run tetap berhasil menyelesaikan import!
    res = runner.run()
    assert res["total"] == 100
    status_data = runner.status()
    assert status_data["status"] == "success"
    assert status_data["history_health"] == "error"
    assert status_data["history_warning"] is not None


def test_storage_failure_does_not_halt_import(tmp_path):
    """Temuan 2: Kegagalan penulisan history tidak boleh menggagalkan import yang sukses."""
    mock_history = MagicMock()
    mock_history.start_run.side_effect = OSError("Disk full error")
    mock_history.finish_run.side_effect = OSError("Disk full error")
    mock_history.health = "error"
    mock_history.last_error = "Disk full error"

    settings = SimpleNamespace(
        state_dir=tmp_path / "state",
    )
    fake_importer = MagicMock()
    fake_importer.run.return_value = {"total": 500}
    runner = JobRunner(settings, lambda: fake_importer, history_store=mock_history)

    # Import tetap berjalan sukses dan mengembalikan hasil
    res = runner.run()
    assert res["total"] == 500
    assert runner.status()["status"] == "success"


def test_retry_start_run_does_not_duplicate(tmp_path):
    """Temuan 2: Retry atau recovery start_run dengan run_id yang sama tidak membuat duplikat."""
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)

    r1 = store.start_run("run_retry_test", job_type="normal", trigger="cli")
    assert r1 is not None

    # Panggil start_run lagi dengan run_id yang sama
    r2 = store.start_run("run_retry_test", job_type="normal", trigger="cli")
    assert r2 is not None

    runs, total = store.list_runs()
    matching = [r for r in runs if r["run_id"] == "run_retry_test"]
    assert len(matching) == 1
    assert total == 1


def test_phase_preserved_on_failed_run(tmp_path):
    """Temuan 5: Run yang gagal pada tahap cod_compacting harus mempertahankan tahap tersebut."""
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)

    store.start_run("run_fail_stage", job_type="normal", trigger="scheduled")
    store.update_progress("run_fail_stage", phase="cod_compacting", message="Sedang memadatkan sheet COD: 85 baris")

    store.finish_run(
        "run_fail_stage",
        status="failed",
        error="Google Sheets API 500 Internal Error with access_token=SYNTHETIC_TOKEN",
        duration_seconds=18.4,
    )

    r = store.get_run("run_fail_stage")
    assert r["status"] == "failed"
    # Tahap terakhir harus tetap cod_compacting, bukan di-overwrite 'failed'!
    assert r["phase"] == "cod_compacting"
    assert r["last_phase"] == "cod_compacting"
    assert r["message"] == "Sedang memadatkan sheet COD: 85 baris"
    assert "Google Sheets API 500" in r["error"]
    assert "SYNTHETIC_TOKEN" not in r["error"]
    assert "[REDACTED_TOKEN]" in r["error"]


def test_interrupted_run_preserves_last_phase(tmp_path):
    """Temuan 5: Run interrupted harus mempertahankan tahap terakhir dan tidak mengarang finished_at."""
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path, lock_checker=lambda: False)

    store.start_run("run_interrupted_stage", job_type="normal", trigger="startup", pid=9999999)
    store.update_progress("run_interrupted_stage", phase="fetch", message="Mengunduh data tab ADYAWINSA")

    count = store.reconcile_interrupted_runs()
    assert count == 1

    r = store.get_run("run_interrupted_stage")
    assert r["status"] == "interrupted"
    assert r["phase"] == "fetch"
    assert r["last_phase"] == "fetch"
    assert r["finished_at"] is None
    assert r["duration_seconds"] is None
    assert "terputus" in r["message"]


def test_schema_migration_preserves_existing_data(tmp_path):
    """Temuan 5: Migrasi schema (last_phase, last_message) tidak boleh menghapus data riwayat yang sudah ada."""
    db_path = tmp_path / "history.db"

    # Buat schema lama tanpa kolom last_phase dan last_message
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute("""
            CREATE TABLE import_runs (
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
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            INSERT INTO import_runs (run_id, job_type, trigger, started_at, status, created_at, updated_at)
            VALUES ('legacy_run_1', 'normal', 'cli', '2026-11-15T00:00:00Z', 'success', '2026-11-15T00:00:00Z', '2026-11-15T00:00:00Z')
        """)
    conn.close()

    # Buka dengan HistoryStore baru (menjalankan init_db & safe migration)
    store = HistoryStore(db_path)
    assert store.health == "healthy"

    # Verifikasi data lama tetap ada
    legacy = store.get_run("legacy_run_1")
    assert legacy is not None
    assert legacy["run_id"] == "legacy_run_1"
    assert legacy["status"] == "success"

    # Verifikasi kolom baru dapat digunakan
    store.start_run("new_run_2", job_type="normal", trigger="cli")
    store.update_progress("new_run_2", phase="normalize", message="Normalisasi data")
    new_r = store.get_run("new_run_2")
    assert new_r["last_phase"] == "normalize"


def test_wal_sidecars_persist_for_read_only_dashboard(tmp_path):
    """A reader without directory write access needs readable WAL sidecars."""
    import sqlite3

    db = tmp_path / "public" / "history.db"
    store = HistoryStore(db)
    store.start_run("reader-test")
    for suffix in ("-wal", "-shm"):
        sidecar = db.with_name(db.name + suffix)
        assert sidecar.exists()
        assert sidecar.stat().st_mode & 0o444 == 0o444
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as reader:
        assert reader.execute("SELECT run_id FROM import_runs").fetchone()[0] == "reader-test"
    store.finish_run("reader-test", "success", result={"total": 1})
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as reader:
        assert reader.execute("SELECT status FROM import_runs").fetchone()[0] == "success"
    store.close()
