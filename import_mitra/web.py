import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .google_api import GoogleSheets
from .importer import Importer
from .jobs import JobRunner, Worker
from .settings import Settings, load_config


def make_importer(settings):
    api = GoogleSheets(settings.credentials_file, settings.requests_per_minute)
    return Importer(api, load_config(settings.config_path), interval_seconds=settings.interval_seconds,
                    public_url=settings.public_url)


def create_app(settings=None, worker=None):
    settings = settings or Settings.load()
    if len(settings.admin_password) < 16:
        raise ValueError("Isi MITRA_ADMIN_PASSWORD minimal 16 karakter di .env sebelum menjalankan server.")
    load_config(settings.config_path)
    worker = worker or Worker(JobRunner(settings, lambda: make_importer(settings)),
                              settings.interval_seconds, settings.refresh_on_start)

    @asynccontextmanager
    async def lifespan(app):
        worker.start()
        yield
        worker.close()

    app = FastAPI(title="Import Mitra VPS", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    auth = HTTPBasic()

    def authorize(login: Annotated[HTTPBasicCredentials, Depends(auth)]):
        valid_user = secrets.compare_digest(login.username.encode(), settings.admin_user.encode())
        valid_password = secrets.compare_digest(login.password.encode(), settings.admin_password.encode())
        if not (valid_user and valid_password):
            raise HTTPException(401, "Username/password salah.", headers={"WWW-Authenticate": "Basic"})

    @app.get("/healthz")
    def health():
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(authorize)])
    def dashboard():
        return HTMLResponse(Path(__file__).with_name("dashboard.html").read_text(),
                            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                     "X-Content-Type-Options": "nosniff"})

    @app.get("/api/status", dependencies=[Depends(authorize)])
    def status():
        return worker.status()

    @app.post("/api/refresh", status_code=202, dependencies=[Depends(authorize)])
    def refresh(x_mitra_action: str = Header(default="")):
        # A custom header prevents cross-site HTML forms from triggering a refresh.
        # CORS is deliberately not enabled, so a foreign page cannot set this header.
        if x_mitra_action != "refresh":
            raise HTTPException(403, "Header X-Mitra-Action: refresh diperlukan.")
        if not worker.request_refresh():
            raise HTTPException(409, "Refresh masih berjalan; pantau statusnya.")
        return {"accepted": True, "message": "Refresh dimulai. Pantau status di halaman ini."}

    return app

