from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from import_mitra.jobs import BusyError, JobRunner, Worker, exclusive_job, next_deadline
from import_mitra.settings import Settings
from import_mitra.web import create_app


def settings(tmp_path):
    return Settings(config_path=Path(__file__).parents[1] / "config/mitra.json",
                    credentials_file=tmp_path / "no-token.json", state_dir=tmp_path,
                    refresh_on_start=False, admin_password="test-password-at-least-16")


def test_cross_process_job_lock_and_missed_intervals(tmp_path):
    with exclusive_job(tmp_path), pytest.raises(BusyError), exclusive_job(tmp_path):
        pass
    with exclusive_job(tmp_path):
        pass
    assert next_deadline(100, 100, 3600) == 3700
    assert next_deadline(100, 7300, 3600) == 10900
    assert next_deadline(4000, 100, 3600) == 4000


def test_runner_records_failure_then_recovers(tmp_path):
    s = settings(tmp_path)
    def bad_run(*args, **kwargs):
        raise ValueError("Source error")
    runner = JobRunner(s, lambda: SimpleNamespace(run=bad_run))
    with pytest.raises(ValueError):
        runner.run()
    assert runner.status()["status"] == "failed"
    assert "last_success_at" not in runner.status()
    runner.importer_factory = lambda: SimpleNamespace(run=lambda *a, **kw: {"total": 1})
    assert runner.run() == {"total": 1}
    assert runner.status()["last_success_at"]


def test_manual_refresh_is_not_queued_twice(tmp_path):
    runner = JobRunner(settings(tmp_path), lambda: None)
    worker = Worker(runner, refresh_on_start=False)
    assert worker.request_refresh() is True
    assert worker.request_refresh() is False


def test_worker_runs_automatic_start_and_rejects_manual_overlap(tmp_path):
    started, release = Event(), Event()
    calls = []

    def run():
        calls.append(1)
        started.set()
        release.wait(timeout=2)

    runner = SimpleNamespace(settings=settings(tmp_path), run=run)
    worker = Worker(runner, interval_seconds=3600, refresh_on_start=True)
    worker.start()
    try:
        assert started.wait(timeout=1)
        assert worker.request_refresh() is False
    finally:
        release.set()
        worker.close()
    assert calls == [1]


def test_worker_manual_refresh_wakes_hourly_sleep(tmp_path):
    completed = Event()
    runner = SimpleNamespace(settings=settings(tmp_path), run=completed.set)
    worker = Worker(runner, interval_seconds=3600, refresh_on_start=False)
    worker.start()
    try:
        assert worker.request_refresh() is True
        assert completed.wait(timeout=1)
    finally:
        worker.close()


def test_web_requires_auth_csrf_header_and_returns_409_when_busy(tmp_path):
    s = settings(tmp_path)
    calls = []
    worker = SimpleNamespace(start=lambda: None, close=lambda: None,
                             status=lambda: {"running": False, "status": "idle"},
                             request_refresh=lambda: calls.append(1) or len(calls) == 1)
    with TestClient(create_app(s, worker)) as client:
        assert client.get("/healthz").json() == {"ok": True}
        assert client.get("/").status_code == 401
        assert client.get("/api/status").status_code == 401
        assert client.post("/api/refresh", headers={"X-Mitra-Action": "refresh"}).status_code == 401
        client.auth = (s.admin_user, s.admin_password)
        assert client.get("/").status_code == 200
        assert client.post("/api/refresh").status_code == 403
        assert client.post("/api/refresh", headers={"X-Mitra-Action": "refresh"}).status_code == 202
        assert client.post("/api/refresh", headers={"X-Mitra-Action": "refresh"}).status_code == 409

