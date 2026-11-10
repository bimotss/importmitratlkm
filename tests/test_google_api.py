import json
from types import SimpleNamespace

import pytest
import requests

from import_mitra.google_api import GoogleAPIError, GoogleSheets


def api_with_responses(monkeypatch, responses):
    clock = [100.0]
    sleeps, calls = [], []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("import_mitra.google_api.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("import_mitra.google_api.time.sleep", sleep)
    monkeypatch.setattr("import_mitra.google_api.random.random", lambda: 0)
    api = object.__new__(GoogleSheets)
    api.session = SimpleNamespace(request=request)
    api.interval, api.last = 60 / 45, {"read": 0.0, "write": 0.0}
    return api, calls, sleeps


def response(status, payload):
    return SimpleNamespace(ok=status < 400, status_code=status, json=lambda: payload)


def test_quota_retries_after_a_minute_and_writes_use_raw(monkeypatch):
    api, calls, sleeps = api_with_responses(monkeypatch, [
        response(429, {"error": {"message": "quota exceeded"}}),
        response(200, {"updatedCells": 1}),
    ])
    assert api.write("target", "'data'!A1", [["=TEXT,NOT,A,FORMULA"]]) == {"updatedCells": 1}
    assert 60 in sleeps
    assert calls[1][1]["params"] == {"valueInputOption": "RAW"}
    assert calls[1][1]["json"]["values"] == [["=TEXT,NOT,A,FORMULA"]]


def test_permission_error_is_not_retried(monkeypatch):
    api, calls, _ = api_with_responses(monkeypatch, [response(403, {"error": {"message": "denied"}})])
    with pytest.raises(GoogleAPIError, match="403"):
        api.read("source", "'Data'")
    assert len(calls) == 1


def test_ambiguous_create_is_not_retried_but_commit_can_be(monkeypatch):
    api, calls, _ = api_with_responses(monkeypatch, [requests.ConnectionError("connection lost")])
    with pytest.raises(GoogleAPIError):
        api.batch("target", [{"addSheet": {}}], idempotent=False)
    assert len(calls) == 1
    api, calls, _ = api_with_responses(monkeypatch, [requests.ConnectionError(), response(200, {})])
    assert api.batch("target", [{"copyPaste": {}}]) == {}
    assert len(calls) == 2


def test_oauth_token_format_from_auth_command_is_loadable(tmp_path, monkeypatch):
    info = {"type": "authorized_user", "client_id": "test.apps.googleusercontent.com",
            "client_secret": "synthetic-secret", "refresh_token": "synthetic-refresh-token"}
    path = tmp_path / "google-token.json"
    path.write_text(json.dumps(info))
    auth = []
    monkeypatch.setattr("import_mitra.google_api.AuthorizedSession", lambda c: auth.append(c) or None)
    GoogleSheets(path)
    assert auth[0].refresh_token == "synthetic-refresh-token"


def test_rate_limits_reads_and_writes_separately(monkeypatch):
    api, calls, sleeps = api_with_responses(monkeypatch, [response(200, {}) for _ in range(4)])
    api.read("s", "Data")
    api.write("t", "A1", [[1]])
    api.read("s", "Data")
    api.write("t", "A2", [[2]])
    assert len(calls) == 4
    assert sum(sleeps) == pytest.approx(60 / 45)

