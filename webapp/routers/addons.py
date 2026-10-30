"""Phase-2 add-ons: API tokens (optional future QBO integration)."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from webapp.database import get_db
from webapp.models import ApiToken, User
from webapp.security import client_ip, require
from webapp.services.audit_service import write_audit
from webapp.templating import templates_ctx as templates

router = APIRouter(prefix="/settings/tokens", tags=["tokens"])


@router.get("", response_class=HTMLResponse)
def list_tokens(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("settings.manage")),
):
    tokens = (
        db.query(ApiToken)
        .filter_by(user_id=user.id)
        .order_by(ApiToken.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "settings/tokens.html",
        {"request": request, "user": user, "tokens": tokens, "new_token": None},
    )


@router.post("/create")
def create_token(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("settings.manage")),
    name: str = Form(...),
):
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    row = ApiToken(user_id=user.id, name=name.strip(), token_hash=token_hash)
    db.add(row)
    db.commit()
    write_audit(db, user, "tokens.create", "api_token", row.id, name, client_ip(request))
    tokens = (
        db.query(ApiToken)
        .filter_by(user_id=user.id)
        .order_by(ApiToken.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "settings/tokens.html",
        {
            "request": request,
            "user": user,
            "tokens": tokens,
            "new_token": raw,
            "flash": "Copy the token now — it will not be shown again.",
        },
    )


@router.post("/{token_id}/revoke")
def revoke_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("settings.manage")),
):
    row = db.query(ApiToken).filter_by(id=token_id, user_id=user.id).first()
    if row:
        row.is_active = False
        db.commit()
        write_audit(db, user, "tokens.revoke", "api_token", token_id, row.name, client_ip(request))
    return RedirectResponse("/settings/tokens", status_code=303)
