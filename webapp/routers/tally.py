"""Tally operations: Cash & Deposit Match and the Credit Sales Report.

Every view re-parses the stored upload rather than caching parsed rows in
process memory, so the pages behave correctly under multiple workers and
survive a restart.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from webapp.config import OUTPUT_DIR, UPLOAD_DIR
from webapp.database import get_db
from webapp.models import TallyOpeningBalance, TallyRun, User
from webapp.security import client_ip, require
from webapp.services import tally_bridge as bridge
from webapp.services.audit_service import write_audit
from webapp.templating import templates_ctx as templates

router = APIRouter(prefix="/tally", tags=["tally"])

TALLY_UPLOAD_DIR = UPLOAD_DIR / "Tally"
MAX_UPLOAD_BYTES = 40 * 1024 * 1024  # nginx allows 50M; fail early with a clear message.


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _store_upload(upload: UploadFile) -> Path:
    """Save an upload safely. Path(...).name strips any directory component."""
    name = Path(upload.filename or "upload.csv").name or "upload.csv"
    TALLY_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = _unique_path(TALLY_UPLOAD_DIR / name)
    size = 0
    too_big = False
    with dest.open("wb") as handle:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                too_big = True
                break
            handle.write(chunk)
    if too_big:
        dest.unlink(missing_ok=True)
        raise bridge.TallyError(
            f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
            "Export a narrower date range from QuickBooks."
        )
    if size == 0:
        dest.unlink(missing_ok=True)
        raise bridge.TallyError("That file is empty.")
    return dest


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    except ValueError:
        return []


def _owned_run(db: Session, run_id: int, user: User, tool: Optional[str] = None) -> Optional[TallyRun]:
    run = db.query(TallyRun).filter_by(id=run_id).first()
    if not run or run.user_id != user.id:
        return None
    if tool and run.tool != tool:
        return None
    return run


def _recent(db: Session, tool: str, user: User) -> list[TallyRun]:
    return (
        db.query(TallyRun)
        .filter(TallyRun.tool == tool, TallyRun.user_id == user.id)
        .order_by(TallyRun.created_at.desc())
        .limit(10)
        .all()
    )


def _openings(db: Session, company: str, as_of: date) -> dict:
    rows = (
        db.query(TallyOpeningBalance)
        .filter_by(company=company, as_of=as_of.isoformat())
        .all()
    )
    return {(r.location, r.account): Decimal(r.amount) for r in rows}


def _context(request: Request, user: User, tool_key: str, **extra: Any) -> dict:
    ctx = {
        "request": request,
        "user": user,
        "tool_key": tool_key,
        "tool": bridge.TALLY_TOOLS[tool_key],
        "run": None,
        "report": None,
        "error": None,
        "display": bridge.display,
        "to_iso": bridge.to_iso,
    }
    ctx.update(extra)
    return ctx


def _template(tool_key: str) -> str:
    return f"tally/{tool_key}.html"


def _build(db: Session, run: TallyRun) -> dict:
    """Recompute a run from its stored file and saved options."""
    path = Path(run.input_path)
    if not path.exists():
        raise bridge.TallyError(
            "The uploaded file is no longer on the server. Upload it again."
        )
    date_from = bridge.parse_input_date(run.date_from, "From date")
    date_to = bridge.parse_input_date(run.date_to, "To date")
    if run.tool == "cash_deposit":
        return bridge.cash_deposit_report(path, date_from, date_to, _json_list(run.locations))
    company, _ = bridge.load_credit_sales(path)
    report = bridge.credit_sales_report(
        path, date_from, date_to, _json_list(run.locations), _json_list(run.accounts),
        _openings(db, company, date_from - timedelta(days=1)),
        run.prefix, run.receipt_type,
    )
    if run.company != report["company"]:
        run.company = report["company"]
        db.commit()
    return report


def _render(request: Request, db: Session, user: User, run: TallyRun):
    try:
        report = _build(db, run)
    except bridge.TallyError as exc:
        return templates.TemplateResponse(
            _template(run.tool),
            _context(request, user, run.tool, run=run, error=str(exc),
                     recent=_recent(db, run.tool, user)),
        )
    return templates.TemplateResponse(
        _template(run.tool),
        _context(request, user, run.tool, run=run, report=report,
                 recent=_recent(db, run.tool, user)),
    )


# ─────────────────────────────── pages ───────────────────────────────

@router.get("/{tool_key}", response_class=HTMLResponse)
def tally_page(
    tool_key: str,
    request: Request,
    run_id: int = Query(0),
    db: Session = Depends(get_db),
    user: User = Depends(require("tally.run")),
):
    if tool_key not in bridge.TALLY_TOOLS:
        return RedirectResponse("/tally/cash_deposit", status_code=303)
    if run_id:
        run = _owned_run(db, run_id, user, tool_key)
        if run:
            return _render(request, db, user, run)
    return templates.TemplateResponse(
        _template(tool_key),
        _context(request, user, tool_key, recent=_recent(db, tool_key, user),
                 error=request.query_params.get("error")),
    )


@router.post("/{tool_key}/run", response_class=HTMLResponse)
async def tally_run(
    tool_key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("tally.run")),
    file: UploadFile = File(...),
    date_from: str = Form(""),
    date_to: str = Form(""),
    prefix: str = Form("DS"),
    receipt_type: str = Form("Deposit"),
):
    if tool_key not in bridge.TALLY_TOOLS:
        return RedirectResponse("/tally/cash_deposit", status_code=303)
    try:
        start = bridge.parse_input_date(date_from, "From date")
        end = bridge.parse_input_date(date_to, "To date")
        if start > end:
            raise bridge.TallyError("The From date must be on or before the To date.")
        dest = _store_upload(file)
    except bridge.TallyError as exc:
        return templates.TemplateResponse(
            _template(tool_key),
            _context(request, user, tool_key, error=str(exc),
                     recent=_recent(db, tool_key, user)),
        )

    run = TallyRun(
        tool=tool_key, user_id=user.id, input_path=str(dest), file_name=dest.name,
        date_from=bridge.to_us(start), date_to=bridge.to_us(end),
        locations="", accounts="",
        prefix=(prefix or "DS").strip() or "DS",
        receipt_type=(receipt_type or "Deposit").strip() or "Deposit",
        status="completed",
    )
    db.add(run)
    db.commit()

    try:
        report = _build(db, run)
    except bridge.TallyError as exc:
        run.status = "failed"
        run.message = str(exc)
        db.commit()
        write_audit(db, user, "tally.run", "tally_run", run.id,
                    f"{tool_key}: failed - {exc}", client_ip(request))
        return templates.TemplateResponse(
            _template(tool_key),
            _context(request, user, tool_key, run=run, error=str(exc),
                     recent=_recent(db, tool_key, user)),
        )

    run.company = report.get("company", "")
    run.message = _summary_line(tool_key, report)
    db.commit()
    write_audit(db, user, "tally.run", "tally_run", run.id,
                f"{tool_key}: {run.message}", client_ip(request))
    return templates.TemplateResponse(
        _template(tool_key),
        _context(request, user, tool_key, run=run, report=report,
                 recent=_recent(db, tool_key, user)),
    )


@router.post("/{tool_key}/refine", response_class=HTMLResponse)
async def tally_refine(
    tool_key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("tally.run")),
):
    form = await request.form()
    run = _owned_run(db, int(form.get("run_id") or 0), user, tool_key)
    if not run:
        return RedirectResponse(f"/tally/{tool_key}", status_code=303)

    if form.get("date_from"):
        run.date_from = str(form.get("date_from"))
    if form.get("date_to"):
        run.date_to = str(form.get("date_to"))
    if tool_key == "credit_sales":
        run.prefix = (str(form.get("prefix") or "DS")).strip() or "DS"
        run.receipt_type = (str(form.get("receipt_type") or "Deposit")).strip() or "Deposit"
    # The checkbox group is only present when the picker was submitted. Clearing
    # every box is a mistake, not a request for everything: say so rather than
    # silently restoring the full list (the desktop app warns the same way).
    problem = ""
    if "locations_present" in form:
        chosen = [str(v) for v in form.getlist("locations")]
        if not chosen:
            problem = "Select at least one location."
        else:
            run.locations = json.dumps(chosen)
    if not problem and "accounts_present" in form:
        chosen = [str(v) for v in form.getlist("accounts")]
        if not chosen:
            problem = "Select at least one account."
        else:
            run.accounts = json.dumps(chosen)
    if problem:
        db.rollback()
        try:
            report = _build(db, run)
        except bridge.TallyError:
            report = None
        return templates.TemplateResponse(
            _template(tool_key),
            _context(request, user, tool_key, run=run, report=report,
                     error=problem, recent=_recent(db, tool_key, user)),
        )
    db.commit()
    write_audit(db, user, "tally.refine", "tally_run", run.id,
                f"{tool_key}: {run.date_from}-{run.date_to}", client_ip(request))
    return _render(request, db, user, run)


@router.post("/credit_sales/openings", response_class=HTMLResponse)
async def tally_openings(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("tally.run")),
):
    form = await request.form()
    run = _owned_run(db, int(form.get("run_id") or 0), user, "credit_sales")
    if not run:
        return RedirectResponse("/tally/credit_sales", status_code=303)

    start = bridge.parse_input_date(run.date_from, "From date")
    as_of = (start - timedelta(days=1)).isoformat()
    company = run.company or ""
    saved = removed = 0
    try:
        for key in form.keys():
            if not key.startswith("open_"):
                continue
            index = key[len("open_"):]
            location = str(form.get(f"loc_{index}") or "")
            account = str(form.get(f"acct_{index}") or "")
            if not location or not account:
                continue
            existing = (
                db.query(TallyOpeningBalance)
                .filter_by(company=company, as_of=as_of, location=location, account=account)
                .first()
            )
            amount = bridge.money_or_none(str(form.get(key) or ""))
            if amount is None:
                # Blank means unknown, never zero: drop the row entirely.
                if existing:
                    db.delete(existing)
                    removed += 1
                continue
            if existing:
                existing.amount = str(amount)
                existing.updated_by = user.id
            else:
                db.add(TallyOpeningBalance(
                    company=company, as_of=as_of, location=location, account=account,
                    amount=str(amount), updated_by=user.id,
                ))
            saved += 1
        db.commit()
    except bridge.TallyError as exc:
        db.rollback()
        try:
            report = _build(db, run)
        except bridge.TallyError:
            report = None
        return templates.TemplateResponse(
            _template("credit_sales"),
            _context(request, user, "credit_sales", run=run, report=report,
                     error=str(exc), recent=_recent(db, "credit_sales", user)),
        )

    write_audit(db, user, "tally.openings", "tally_run", run.id,
                f"as of {as_of}: {saved} saved, {removed} cleared", client_ip(request))
    return _render(request, db, user, run)


# ───────────────────────────── downloads ─────────────────────────────

@router.get("/runs/{run_id}/print", response_class=HTMLResponse)
def tally_print(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("tally.download")),
):
    run = _owned_run(db, run_id, user)
    if not run:
        return RedirectResponse("/tally/cash_deposit", status_code=303)
    try:
        report = _build(db, run)
        start = bridge.parse_input_date(run.date_from, "From date")
        end = bridge.parse_input_date(run.date_to, "To date")
        if run.tool == "cash_deposit":
            body = bridge.cash_deposit_html(report, start, end, run.input_path)
        else:
            body = bridge.credit_sales_html(report, start, end, run.input_path)
    except bridge.TallyError as exc:
        return HTMLResponse(f"<p style='font:14px Arial;padding:24px'>{exc}</p>", status_code=400)
    write_audit(db, user, "tally.download", "tally_run", run.id,
                f"{run.tool}: print", client_ip(request))
    return HTMLResponse(body)


@router.get("/runs/{run_id}/pdf")
def tally_pdf(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("tally.download")),
):
    run = _owned_run(db, run_id, user)
    if not run:
        return RedirectResponse("/tally/cash_deposit", status_code=303)
    try:
        report = _build(db, run)
        start = bridge.parse_input_date(run.date_from, "From date")
        end = bridge.parse_input_date(run.date_to, "To date")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if run.tool == "cash_deposit":
            name = f"Cash_Deposit_Reconciliation_{stamp}.pdf"
            out = bridge.cash_deposit_pdf(OUTPUT_DIR / name, report, start, end, run.input_path)
        else:
            name = f"Credit_Sales_Account_Details_{stamp}.pdf"
            out = bridge.credit_sales_pdf(OUTPUT_DIR / name, Path(run.input_path), report,
                                          start, end, run.prefix, run.receipt_type)
    except bridge.TallyError as exc:
        return RedirectResponse(f"/tally/{run.tool}?run_id={run.id}&error={exc}", status_code=303)
    write_audit(db, user, "tally.download", "tally_run", run.id,
                f"{run.tool}: {name}", client_ip(request))
    return FileResponse(str(out), filename=name, media_type="application/pdf")


def _summary_line(tool_key: str, report: dict) -> str:
    if tool_key == "cash_deposit":
        t = report["totals"]
        return (f"{t['rows']} rows | Tender ${t['tender']:,.2f} | "
                f"Deposit ${t['deposit']:,.2f} | Difference ${t['difference']:,.2f}")
    total = report["total"]
    return (f"{len(report['summary'])} location(s) | Sales {bridge.display(total[2])} | "
            f"Receipts {bridge.display(total[3])}")
