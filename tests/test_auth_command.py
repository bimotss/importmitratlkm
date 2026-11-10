import json
import stat
from pathlib import Path
from types import SimpleNamespace

from import_mitra.__main__ import main
from import_mitra.settings import Settings


def test_auth_creates_private_authorized_user_token(tmp_path, monkeypatch):
    settings = Settings(config_path=Path("unused.json"), credentials_file=tmp_path / "secrets" / "token.json",
                        state_dir=tmp_path / "runtime")
    monkeypatch.setattr(Settings, "load", lambda: settings)
    info = {"refresh_token": "synthetic-token", "client_id": "synthetic-id", "client_secret": "synthetic-key"}
    token = SimpleNamespace(to_json=lambda: json.dumps(info))
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return token

    monkeypatch.setattr("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
                        lambda *args: SimpleNamespace(run_local_server=run))
    monkeypatch.setattr("sys.argv", ["import-mitra", "auth", "--client", "unused.json"])
    assert main() == 0
    saved = json.loads(settings.credentials_file.read_text())
    assert saved["type"] == "authorized_user"
    assert stat.S_IMODE(settings.credentials_file.stat().st_mode) == 0o600
    assert calls[0]["access_type"] == "offline"
    assert calls[0]["prompt"] == "consent"
