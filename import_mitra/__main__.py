import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .jobs import BusyError, JobRunner
from .settings import Settings


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Import Mitra Google Sheets dari VPS")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Dashboard + jadwal otomatis setiap jam")
    once = sub.add_parser("once", help="Refresh satu kali dari terminal")
    once.add_argument("--dry-run", action="store_true", help="Baca dan validasi semua sumber tanpa menulis")
    sub.add_parser("check", help="Periksa akses tujuan + semua sumber tanpa menulis")
    auth = sub.add_parser("auth", help="Login Google sekali dari laptop; token dapat dipindahkan ke VPS")
    auth.add_argument("--client", required=True, type=Path, help="JSON OAuth client jenis Desktop app")
    auth.add_argument("--port", type=int, default=8765)
    auth.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        settings = Settings.load()
        if args.command == "auth":
            from google_auth_oauthlib.flow import InstalledAppFlow

            from .google_api import SCOPES
            flow = InstalledAppFlow.from_client_secrets_file(str(args.client), SCOPES)
            token = flow.run_local_server(host="127.0.0.1", port=args.port, open_browser=not args.no_browser,
                                          access_type="offline", prompt="consent", authorization_prompt_message=
                                          "Buka URL ini di browser untuk login:\n{url}")
            settings.credentials_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(settings.credentials_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as handle:
                info = json.loads(token.to_json())
                info["type"] = "authorized_user"
                handle.write(json.dumps(info))
            settings.credentials_file.chmod(0o600)
            print(f"Token tersimpan di {settings.credentials_file}. Jangan masukkan ke Git.")
        elif args.command == "serve":
            import uvicorn

            from .web import create_app
            uvicorn.run(create_app(settings), host=settings.host, port=settings.port, workers=1,
                        proxy_headers=True, forwarded_allow_ips="127.0.0.1", access_log=False)
        else:
            from .web import make_importer
            if args.command == "check":
                importer = make_importer(settings)
                importer.api.metadata(importer.target)
                result = importer.run(dry_run=True)
            else:
                runner = JobRunner(settings, lambda: make_importer(settings))
                result = runner.run(dry_run=args.dry_run)
            print(json.dumps(result, indent=2))
    except BusyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 -- CLI boundary: report a failed job and return a nonzero code.
        print(f"Gagal: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

