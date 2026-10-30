"""Dashboard with KPI cards, recent activity, and analytics."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload

from webapp.config import WORK_TYPES
from webapp.database import get_db
from webapp.models import AuditLog, FileRecord, User
from webapp.security import require
from webapp.templating import templates_ctx as templates

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("dashboard.view")),
):
    today = date.today()
    month_prefix = today.strftime("%m/")
    month_suffix = today.strftime("/%Y")

    counts = {}
    for wt in WORK_TYPES:
        counts[wt] = (
            db.query(FileRecord)
            .filter(FileRecord.work_type == wt)
            .filter(FileRecord.work_date.like(f"{month_prefix}%{month_suffix}"))
            .count()
        )
        if counts[wt] == 0:
            # also count by created_at month if work_date empty/mismatched
            start = today.replace(day=1)
            if today.month == 12:
                end = today.replace(year=today.year + 1, month=1, day=1)
            else:
                end = today.replace(month=today.month + 1, day=1)
            counts[wt] = (
                db.query(FileRecord)
                .filter(FileRecord.work_type == wt)
                .filter(FileRecord.created_at >= start, FileRecord.created_at < end)
                .count()
            )

    recent = (
        db.query(FileRecord)
        .order_by(FileRecord.created_at.desc())
        .limit(8)
        .all()
    )
    needs_review = (
        db.query(FileRecord)
        .filter(FileRecord.status.in_(["Needs Review", "On Hold"]))
        .order_by(FileRecord.updated_at.desc())
        .limit(5)
        .all()
    )
    activity = (
        db.query(AuditLog)
        .options(joinedload(AuditLog.user))
        .order_by(AuditLog.created_at.desc())
        .limit(12)
        .all()
    )

    # Analytics: last 6 months file counts
    chart_labels = []
    chart_values = []
    for i in range(5, -1, -1):
        d = today.replace(day=1) - timedelta(days=30 * i)
        label = d.strftime("%b %Y")
        chart_labels.append(label)
        m_start = d.replace(day=1)
        if m_start.month == 12:
            m_end = m_start.replace(year=m_start.year + 1, month=1, day=1)
        else:
            m_end = m_start.replace(month=m_start.month + 1, day=1)
        chart_values.append(
            db.query(FileRecord)
            .filter(FileRecord.created_at >= m_start, FileRecord.created_at < m_end)
            .count()
        )

    by_location = (
        db.query(FileRecord.location_name, FileRecord.id)
        .all()
    )
    loc_counts: dict[str, int] = defaultdict(int)
    for name, _ in by_location:
        loc_counts[name or "All"] += 1
    top_locations = sorted(loc_counts.items(), key=lambda x: -x[1])[:8]

    status_counts = defaultdict(int)
    for (st,) in db.query(FileRecord.status).all():
        status_counts[st or "Received"] += 1

    return templates.TemplateResponse(
        "dashboard/index.html",
        {
            "request": request,
            "user": user,
            "counts": counts,
            "work_types": WORK_TYPES,
            "recent": recent,
            "needs_review": needs_review,
            "activity": activity,
            "chart_labels": chart_labels,
            "chart_values": chart_values,
            "top_locations": top_locations,
            "status_counts": dict(status_counts),
        },
    )
