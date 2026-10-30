"""Audit log / activity history."""
from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session, joinedload

from webapp.database import get_db
from webapp.models import AuditLog, User
from webapp.policy import has_permission
from webapp.security import client_ip, require
from webapp.services.audit_service import write_audit
from webapp.templating import templates_ctx as templates

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_class=HTMLResponse)
def audit_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("audit.view_own")),
    action: str = Query(""),
    entity_type: str = Query(""),
    user_id: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    page: int = Query(1, ge=1),
):
    query = db.query(AuditLog).options(joinedload(AuditLog.user))
    if not has_permission(db, user, "audit.view_all"):
        query = query.filter(AuditLog.user_id == user.id)
    elif user_id.strip().isdigit():
        query = query.filter(AuditLog.user_id == int(user_id))
    if action.strip():
        query = query.filter(AuditLog.action.ilike(f"%{action.strip()}%"))
    if entity_type.strip():
        query = query.filter(AuditLog.entity_type == entity_type.strip())
    if date_from.strip():
        try:
            query = query.filter(
                AuditLog.created_at >= datetime.strptime(date_from, "%Y-%m-%d")
            )
        except ValueError:
            pass
    if date_to.strip():
        try:
            query = query.filter(
                AuditLog.created_at
                <= datetime.strptime(date_to + " 23:59:59", "%Y-%m-%d %H:%M:%S")
            )
        except ValueError:
            pass

    total = query.count()
    per_page = 40
    rows = (
        query.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    users = db.query(User).order_by(User.username).all() if has_permission(db, user, "audit.view_all") else []
    actions = sorted({a[0] for a in db.query(AuditLog.action).distinct() if a[0]})
    return templates.TemplateResponse(
        "audit/list.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "users": users,
            "actions": actions,
            "filters": {
                "action": action,
                "entity_type": entity_type,
                "user_id": user_id,
                "date_from": date_from,
                "date_to": date_to,
                "page": page,
                "pages": max(1, (total + per_page - 1) // per_page),
                "total": total,
            },
        },
    )


@router.get("/export")
def export_audit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("audit.export")),
):
    query = db.query(AuditLog).options(joinedload(AuditLog.user))
    if not has_permission(db, user, "audit.view_all"):
        query = query.filter(AuditLog.user_id == user.id)
    rows = query.order_by(AuditLog.created_at.desc()).limit(5000).all()
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["When", "User", "Action", "Entity", "Entity ID", "Detail", "IP"])
    for r in rows:
        writer.writerow([
            r.created_at,
            r.user.username if r.user else "",
            r.action,
            r.entity_type,
            r.entity_id,
            r.detail,
            r.ip_address,
        ])
    write_audit(db, user, "audit.export", "audit_log", "", f"{len(rows)} rows", client_ip(request))
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'},
    )
