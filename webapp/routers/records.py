"""File records: upload, filter, status, follow-up, export, approve."""
from __future__ import annotations

import csv
import shutil
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from webapp.config import STATUSES, UPLOAD_DIR, WORK_TYPES
from webapp.database import get_db
from webapp.models import FileRecord, Location, User
from webapp.security import client_ip, require
from webapp.services.audit_service import write_audit
from webapp.templating import templates_ctx as templates

router = APIRouter(prefix="/records", tags=["records"])


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


@router.get("", response_class=HTMLResponse)
def list_records(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("records.view")),
    category: str = Query("All"),
    location: str = Query("All"),
    status: str = Query("All"),
    q: str = Query(""),
    page: int = Query(1, ge=1),
):
    query = db.query(FileRecord)
    if category != "All":
        query = query.filter(FileRecord.work_type == category)
    if location != "All":
        query = query.filter(FileRecord.location_name == location)
    if status != "All":
        query = query.filter(FileRecord.status == status)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                FileRecord.file_name.ilike(like),
                FileRecord.follow_up.ilike(like),
                FileRecord.work_type.ilike(like),
            )
        )
    total = query.count()
    per_page = 25
    records = (
        query.order_by(FileRecord.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    locations = sorted(
        {r[0] for r in db.query(FileRecord.location_name).distinct() if r[0]}
    )
    pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(
        "records/list.html",
        {
            "request": request,
            "user": user,
            "records": records,
            "work_types": WORK_TYPES,
            "statuses": STATUSES,
            "locations": locations,
            "filters": {
                "category": category,
                "location": location,
                "status": status,
                "q": q,
                "page": page,
                "pages": pages,
                "total": total,
            },
        },
    )


@router.post("/upload")
async def upload_files(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("records.upload")),
    work_type: str = Form(...),
    location: str = Form("All"),
    files: list[UploadFile] = File(...),
):
    target_dir = UPLOAD_DIR / work_type.replace("/", "-").replace("'", "")
    target_dir.mkdir(parents=True, exist_ok=True)
    loc = location.strip() or "All"
    count = 0
    for uf in files:
        if not uf.filename:
            continue
        dest = _unique_path(target_dir / Path(uf.filename).name)
        with dest.open("wb") as out:
            shutil.copyfileobj(uf.file, out)
        rec = FileRecord(
            work_type=work_type,
            work_date=date.today().strftime("%m/%d/%Y"),
            location_name=loc,
            file_name=dest.name,
            stored_path=str(dest),
            status="Received",
            uploaded_by=user.id,
        )
        db.add(rec)
        count += 1
    db.commit()
    write_audit(
        db, user, "records.upload", "file_record", "",
        f"Uploaded {count} file(s) to {work_type}", client_ip(request),
    )
    return RedirectResponse("/records?flash=uploaded", status_code=303)


@router.post("/{record_id}/status")
def change_status(
    record_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("records.edit")),
    status: str = Form(...),
):
    rec = db.query(FileRecord).filter_by(id=record_id).first()
    if not rec:
        return RedirectResponse("/records", status_code=303)
    if status == "Approved":
        # require approve permission
        from webapp.policy import has_permission
        if not has_permission(db, user, "records.approve"):
            return RedirectResponse("/records?error=approve", status_code=303)
    old = rec.status
    rec.status = status
    db.commit()
    write_audit(
        db, user, "records.status", "file_record", record_id,
        f"{old} -> {status}", client_ip(request),
    )
    try:
        from webapp.services.notify import notify_status_change
        if user.email:
            notify_status_change(user.email, rec.file_name, status)
    except Exception:
        pass
    return RedirectResponse("/records", status_code=303)


@router.post("/{record_id}/follow-up")
def edit_follow_up(
    record_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("records.edit")),
    follow_up: str = Form(""),
):
    rec = db.query(FileRecord).filter_by(id=record_id).first()
    if rec:
        rec.follow_up = follow_up.strip()
        db.commit()
        write_audit(
            db, user, "records.follow_up", "file_record", record_id,
            follow_up[:200], client_ip(request),
        )
    return RedirectResponse("/records", status_code=303)


@router.post("/{record_id}/remove")
def remove_record(
    record_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("records.remove")),
):
    rec = db.query(FileRecord).filter_by(id=record_id).first()
    if rec:
        write_audit(
            db, user, "records.remove", "file_record", record_id,
            rec.file_name, client_ip(request),
        )
        db.delete(rec)
        db.commit()
    return RedirectResponse("/records", status_code=303)


@router.get("/export")
def export_tracker(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("records.export")),
):
    rows = db.query(FileRecord).order_by(FileRecord.created_at.desc()).all()
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["Work Type", "Date", "Location", "File", "Status", "Discrepancy", "Follow-up", "Path"]
    )
    for r in rows:
        writer.writerow([
            r.work_type, r.work_date, r.location_name, r.file_name,
            r.status, r.discrepancy, r.follow_up, r.stored_path,
        ])
    write_audit(db, user, "records.export", "file_record", "", f"{len(rows)} rows", client_ip(request))
    buf.seek(0)
    filename = f"PrimeLedgerAI_Tracker_{date.today():%Y%m%d}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
