"""Jinja2 templates with RBAC helpers."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from webapp.config import APP_NAME, APP_VERSION
from webapp.database import SessionLocal
from webapp.policy import has_permission
from webapp.security import get_current_user

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _can(request: Request, code: str) -> bool:
    db = SessionLocal()
    try:
        user = get_current_user(request, db)
        if not user:
            return False
        return has_permission(db, user, code)
    finally:
        db.close()


templates.env.globals["app_name"] = APP_NAME
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["can"] = lambda code: False  # placeholder; set per-request


class ContextTemplates:
    """Wrapper that injects can() bound to the current request."""

    def TemplateResponse(self, name, context, status_code=200):
        request: Request = context["request"]
        user = context.get("user")

        def can(code: str) -> bool:
            if not user:
                return False
            db = SessionLocal()
            try:
                return has_permission(db, user, code)
            finally:
                db.close()

        context.setdefault("flash", None)
        context.setdefault("error", None)
        # Re-bind can for this render
        templates.env.globals["can"] = can
        # Starlette >=0.44 requires request as the first positional argument
        return templates.TemplateResponse(request, name, context, status_code=status_code)


templates_ctx = ContextTemplates()
