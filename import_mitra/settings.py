import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    config_path: Path
    credentials_file: Path
    state_dir: Path
    interval_seconds: int = 3600
    refresh_on_start: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    admin_user: str = "admin"
    admin_password: str = ""
    public_url: str = "http://localhost:8080"
    requests_per_minute: int = 45

    @classmethod
    def load(cls):
        load_dotenv()
        s = cls(
            config_path=Path(os.getenv("MITRA_CONFIG", "config/mitra.json")).resolve(),
            credentials_file=Path(os.getenv("GOOGLE_CREDENTIALS_FILE", "secrets/google-token.json")).resolve(),
            state_dir=Path(os.getenv("MITRA_STATE_DIR", "runtime")).resolve(),
            interval_seconds=int(os.getenv("MITRA_INTERVAL_SECONDS", "3600")),
            refresh_on_start=os.getenv("MITRA_REFRESH_ON_START", "true").lower() == "true",
            host=os.getenv("MITRA_HOST", "127.0.0.1"),
            port=int(os.getenv("MITRA_PORT", "8080")),
            admin_user=os.getenv("MITRA_ADMIN_USER", "admin"),
            admin_password=os.getenv("MITRA_ADMIN_PASSWORD", ""),
            public_url=os.getenv("MITRA_PUBLIC_URL", "http://localhost:8080").rstrip("/"),
            requests_per_minute=int(os.getenv("MITRA_API_REQUESTS_PER_MINUTE", "45")),
        )
        if s.interval_seconds < 60:
            raise ValueError("MITRA_INTERVAL_SECONDS minimal 60 detik.")
        if not 1 <= s.requests_per_minute <= 50:
            raise ValueError("MITRA_API_REQUESTS_PER_MINUTE harus 1–50.")
        return s


def load_config(path: Path):
    config = json.loads(path.read_text())
    sources, schemas = config["sources"], config["schemas"]
    titles = [s["title"] for s in sources]
    reserved = {"DATA TIF FO", "DATA TIF RADIO", "DataDisatukan", "Kontrol", "_Import_Status", "_Peta_Kolom"}
    if not sources or len(set(titles)) != len(titles) or any(t in reserved for t in titles):
        raise ValueError("Nama tab mitra kosong, duplikat, atau memakai nama tab sistem.")
    if any(s["group"] not in {"FO", "RADIO"} for s in sources):
        raise ValueError("Jenis mitra harus FO atau RADIO.")
    if any(s["id"] == config["target_spreadsheet_id"] for s in sources):
        raise ValueError("File tujuan tidak boleh menjadi file sumber.")
    for key in ("fo", "radio", "all"):
        if not schemas[key] or len(set(schemas[key])) != len(schemas[key]):
            raise ValueError(f"Format {key} kosong atau mempunyai judul ganda.")
    return config

