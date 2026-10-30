"""PrimeLedgerAI Cloud FastAPI application."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from webapp.config import (
    APP_NAME,
    APP_VERSION,
    OUTPUT_DIR,
    SECRET_KEY,
    SESSION_COOKIE,
    SESSION_HTTPS_ONLY,
    SESSION_MAX_AGE,
    UPLOAD_DIR,
)
from webapp.routers import (
    addons,
    audit,
    auth,
    converters,
    dashboard,
    permissions,
    records,
    settings,
    tally,
    users,
)

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie=SESSION_COOKIE,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(records.router)
app.include_router(converters.router)
app.include_router(tally.router)
app.include_router(users.router)
app.include_router(permissions.router)
app.include_router(audit.router)
app.include_router(settings.router)
app.include_router(addons.router)


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):  # type: ignore[no-untyped-def]
    return RedirectResponse("/?error=forbidden", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}
