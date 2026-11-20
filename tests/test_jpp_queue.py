import json
from types import SimpleNamespace

import pytest

from import_mitra.jobs import BusyError
from import_mitra.jpp_queue import process_upload


@pytest.mark.parametrize("fail", [False, True])
def test_queue_waits_for_import_and_records_terminal_status(tmp_path, monkeypatch, fail):
    (tmp_path / "pending").write_text(json.dumps({"filename": "JPP.xlsx"}))
    (tmp_path / "upload.xlsx").write_bytes(b"test")
    calls = []

    class Runner:
        def __init__(self, _settings, _factory):
            pass

        def run(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise BusyError("active")
            if fail:
                raise ValueError("File tidak valid")
            return {"total": 12, "missing_columns": ["TREG"]}

    monkeypatch.setattr("import_mitra.jpp_queue.JobRunner", Runner)
    monkeypatch.setattr("import_mitra.jpp_queue.time.sleep", lambda seconds: None)
    settings = SimpleNamespace(jpp_upload_dir=tmp_path)
    if fail:
        with pytest.raises(ValueError, match="File tidak valid"):
            process_upload(settings)
    else:
        process_upload(settings)
    state = json.loads((tmp_path / "status.json").read_text())
    assert state["status"] == ("failed" if fail else "success")
    assert len(calls) == 2
    assert calls[-1] == {"trigger": "web", "job_type": "update_jpp"}
    assert not (tmp_path / "pending").exists()
    assert not (tmp_path / "upload.xlsx").exists()
