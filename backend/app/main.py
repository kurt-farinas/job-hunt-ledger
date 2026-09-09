"""Run with: python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1"""

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import router
from app.config.preferences import load_preferences
from app.config.settings import Settings
from app.config.sources import load_sources
from app.db.database import Database
from app.scheduler.jobs import build_scheduler
from app.services.refresh import RefreshService
from app.services.gmail import GmailService


def create_app(settings: Settings | None = None, *, adapter_factories=None) -> FastAPI:
    settings = settings or Settings()
    # These libraries otherwise log credential-bearing Adzuna query strings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # OAuth authorization codes arrive in the callback query string. Keep
    # Uvicorn's request logger from persisting them in local console logs.
    logging.getLogger("uvicorn.access").disabled = True

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        load_sources(settings.config_dir / "sources.json")
        load_preferences(settings.config_dir / "job_preferences.json")
        db = Database(settings.database_url, settings.project_root)
        db.initialize()
        db.recover_interrupted_runs()
        app.state.db = db
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=False,
                                     trust_env=False, headers={"Accept": "application/json"}) as client:
            refresh = RefreshService(db, settings, client, adapter_factories)
            gmail = GmailService(db, settings, client)
            app.state.refresh = refresh
            app.state.gmail = gmail
            scheduler = build_scheduler(refresh, gmail) if settings.scheduler_enabled else None
            app.state.scheduler = scheduler
            if scheduler:
                scheduler.start()
            try:
                yield
            finally:
                if scheduler and scheduler.running:
                    scheduler.shutdown(wait=False)
                await refresh.close()
                db.close()

    # Default Swagger/ReDoc pages load third-party scripts into the API origin.
    # Keep the machine-readable /openapi.json without any external UI assets.
    app = FastAPI(title="Local Job Search Dashboard", version="0.4.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Content-Type"])

    @app.middleware("http")
    async def protect_local_writes(request: Request, call_next):
        try:
            host = urlsplit("//" + request.headers.get("host", ""))
            valid_host = host.hostname in {"localhost", "127.0.0.1", "::1"}
            valid_host = valid_host and not (host.username or host.password or host.path or host.query or host.fragment)
            host.port  # Validate the port as well, including bracketed IPv6.
        except ValueError:
            valid_host = False
        if not valid_host:
            return JSONResponse(status_code=400, content={"error": {"code": "invalid_host", "message": "Only loopback hosts are accepted."}})
        if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
            origin = request.headers.get("origin")
            allowed = set(settings.cors_origins) | {"http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"}
            if (origin is not None and origin not in allowed) or (origin is None and request.headers.get("sec-fetch-site") == "cross-site"):
                return JSONResponse(status_code=403, content={"error": {"code": "origin_not_allowed", "message": "Only local dashboard requests are accepted."}})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {
            "code": "validation_error", "message": "Request validation failed.",
            "details": [{"field": ".".join(str(item) for item in error["loc"]), "message": error["msg"]} for error in exc.errors()],
        }})

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "A local server error occurred."}})

    app.include_router(router)
    return app


app = create_app()
