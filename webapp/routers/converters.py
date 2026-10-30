"""Converter pages that call legacy scripts via converter_bridge."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from webapp.config import OUTPUT_DIR, UPLOAD_DIR
from webapp.database import get_db
from webapp.models import ConversionJob, FileRecord, User
from webapp.security import client_ip, require
from webapp.services import converter_bridge as bridge
from webapp.services.audit_service import write_audit
from webapp.templating import templates_ctx as templates

router = APIRouter(prefix="/converters", tags=["converters"])

# In-memory store for invoice session rows (keyed by user id)
_invoice_sessions: dict[int, dict[str, Any]] = {}


@router.get("/{tool_key}", response_class=HTMLResponse)
def converter_page(
    tool_key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("convert.run")),
):
    tool = bridge.TOOLS.get(tool_key)
    if not tool:
        return RedirectResponse("/", status_code=303)
    recent = (
        db.query(ConversionJob)
        .filter(ConversionJob.tool_name == tool_key, ConversionJob.user_id == user.id)
        .order_by(ConversionJob.created_at.desc())
        .limit(10)
        .all()
    )
    tmpl = "converters/invoice.html" if tool_key == "invoice" else "converters/tool.html"
    ctx = {
        "request": request,
        "user": user,
        "tool_key": tool_key,
        "tool": tool,
        "recent": recent,
        "result": None,
        "invoice_rows": None,
        "accounts": [],
    }
    if tool_key == "invoice":
        sess = _invoice_sessions.get(user.id)
        if sess:
            ctx["invoice_rows"] = sess.get("rows_view")
            ctx["accounts"] = sess.get("accounts", [])
    return templates.TemplateResponse(tmpl, ctx)


@router.post("/{tool_key}/run")
async def run_converter(
    tool_key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("convert.run")),
    file: UploadFile = File(...),
    location: str = Form("Marietta Hotel"),
    mapping: Optional[UploadFile] = File(None),
):
    tool = bridge.TOOLS.get(tool_key)
    if not tool or tool_key == "invoice":
        return RedirectResponse("/converters/invoice", status_code=303)

    upload_dir = UPLOAD_DIR / tool["work_type"].replace("/", "-").replace("'", "")
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / (file.filename or "upload.bin")
    # unique
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}_{datetime.now():%H%M%S}{dest.suffix}")
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    rec = FileRecord(
        work_type=tool["work_type"],
        work_date=datetime.now().strftime("%m/%d/%Y"),
        location_name=location if tool_key == "hotel_revenue" else "All",
        file_name=dest.name,
        stored_path=str(dest),
        status="In Progress",
        uploaded_by=user.id,
    )
    db.add(rec)
    db.flush()

    job = ConversionJob(
        tool_name=tool_key,
        file_record_id=rec.id,
        user_id=user.id,
        input_path=str(dest),
        status="running",
    )
    db.add(job)
    db.commit()

    mapping_path = None
    if mapping and mapping.filename:
        mp = upload_dir / mapping.filename
        with mp.open("wb") as out:
            shutil.copyfileobj(mapping.file, out)
        mapping_path = mp

    out_path = OUTPUT_DIR / f"{dest.stem}_out"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    runner = tool["runner"]
    try:
        if tool_key == "daily_remittance":
            result = runner(dest, out_path.with_suffix(".csv"), mapping_path)
        elif tool_key == "hotel_revenue":
            result = runner(dest, out_path.with_suffix(".csv"), location)
        elif tool_key == "sales_tax":
            result = runner(dest, out_path.with_suffix(".pdf"))
        elif tool_key == "payroll":
            result = runner(dest, out_path.with_suffix(".csv"))
        else:
            result = runner(dest, out_path.with_suffix(".csv"))
    except Exception as exc:
        result = bridge.ConversionResult(False, message=str(exc))

    job.status = "completed" if result.success else "failed"
    job.output_path = result.output_path
    job.message = result.message
    job.discrepancy = result.discrepancy
    job.finished_at = datetime.utcnow()
    rec.status = "Completed" if result.success else "Needs Review"
    rec.discrepancy = f"{result.discrepancy:.2f}"
    db.commit()

    write_audit(
        db, user, "convert.run", "conversion_job", job.id,
        f"{tool_key}: {result.message}", client_ip(request),
    )

    recent = (
        db.query(ConversionJob)
        .filter(ConversionJob.tool_name == tool_key, ConversionJob.user_id == user.id)
        .order_by(ConversionJob.created_at.desc())
        .limit(10)
        .all()
    )
    return templates.TemplateResponse(
        "converters/tool.html",
        {
            "request": request,
            "user": user,
            "tool_key": tool_key,
            "tool": tool,
            "recent": recent,
            "result": result,
        },
    )


@router.get("/jobs/{job_id}/download")
def download_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("convert.download")),
):
    job = db.query(ConversionJob).filter_by(id=job_id).first()
    if not job or not job.output_path or not Path(job.output_path).exists():
        return RedirectResponse("/converters/daily_sales", status_code=303)
    write_audit(db, user, "convert.download", "conversion_job", job_id, job.output_path, client_ip(request))
    return FileResponse(job.output_path, filename=Path(job.output_path).name)


@router.post("/invoice/parse")
async def invoice_parse(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("convert.run")),
    files: list[UploadFile] = File(...),
):
    mod = bridge.invoice_module()
    all_rows = []
    accounts = sorted({a for _, a, _ in mod.DEFAULT_RULES})
    upload_dir = UPLOAD_DIR / "Weekly Dennys Invoice"
    upload_dir.mkdir(parents=True, exist_ok=True)

    for uf in files:
        if not uf.filename:
            continue
        dest = upload_dir / uf.filename
        with dest.open("wb") as out:
            shutil.copyfileobj(uf.file, out)
        try:
            rows, meta = bridge.parse_invoice_pdf(dest)
        except Exception as exc:
            return templates.TemplateResponse(
                "converters/invoice.html",
                {
                    "request": request,
                    "user": user,
                    "tool_key": "invoice",
                    "tool": bridge.TOOLS["invoice"],
                    "recent": [],
                    "result": None,
                    "invoice_rows": None,
                    "accounts": accounts,
                    "error": str(exc),
                },
                status_code=400,
            )
        for r in rows:
            r.source_file = str(dest)
            all_rows.append(r)

    rows_view = []
    for i, r in enumerate(all_rows):
        rows_view.append({
            "idx": i,
            "selected": r.selected,
            "invoice": r.invoice,
            "description": r.description,
            "amount": f"{r.amount:.2f}",
            "account": r.account,
            "location": r.location,
            "restaurant": r.restaurant,
            "journal_date": r.journal_date,
            "journal_no": r.journal_no,
            "page": r.page,
            "file": Path(r.source_file).name,
        })

    _invoice_sessions[user.id] = {
        "rows": all_rows,
        "rows_view": rows_view,
        "accounts": accounts,
    }
    write_audit(db, user, "invoice.parse", "conversion_job", "", f"{len(all_rows)} rows", client_ip(request))
    return templates.TemplateResponse(
        "converters/invoice.html",
        {
            "request": request,
            "user": user,
            "tool_key": "invoice",
            "tool": bridge.TOOLS["invoice"],
            "recent": [],
            "result": None,
            "invoice_rows": rows_view,
            "accounts": accounts,
        },
    )


@router.post("/invoice/export")
async def invoice_export(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("convert.run")),
):
    form = await request.form()
    sess = _invoice_sessions.get(user.id)
    if not sess:
        return RedirectResponse("/converters/invoice", status_code=303)

    rows = sess["rows"]
    selected_indices = set()
    for key in form.keys():
        if key.startswith("sel_"):
            selected_indices.add(int(key.split("_", 1)[1]))
        if key.startswith("acct_"):
            idx = int(key.split("_", 1)[1])
            rows[idx].account = str(form.get(key) or "")

    for i, r in enumerate(rows):
        r.selected = i in selected_indices

    chosen = [r for r in rows if r.selected and r.account.strip()]
    if not chosen:
        return RedirectResponse("/converters/invoice?error=none", status_code=303)

    # Build a minimal MapperApp-like export using the module helpers
    mod = bridge.invoice_module()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"Denny_Invoice_Combined_{datetime.now():%Y%m%d%H%M%S}.csv"

    # Use a lightweight write mirroring MapperApp._write_csv grouping
    from collections import defaultdict
    import csv as csv_mod

    column_mapping = mod.load_column_mapping()
    credit_account, tax_account = bridge.invoice_special_accounts()
    CSV_COLUMNS = mod.CSV_COLUMNS

    output_rows = []
    grand_total = Decimal("0.00")
    journal_keys = list(dict.fromkeys((r.journal_date, r.location, r.journal_no) for r in chosen))
    for journal_date, location, journal_no in journal_keys:
        document_rows = [
            r for r in chosen
            if (r.journal_date, r.location, r.journal_no) == (journal_date, location, journal_no)
        ]
        first = document_rows[0]
        meta = {
            "file": ", ".join(sorted({Path(r.source_file).name for r in document_rows})),
            "date": journal_date,
            "journal": journal_no,
            "restaurant": first.restaurant,
            "location": location,
        }
        grouped: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        invoices: dict[str, set[str]] = defaultdict(set)
        for row in document_rows:
            grouped[row.account] += row.amount
            invoices[row.account].add(row.invoice)
        tax = Decimal("0.00")
        seen_pages = set()
        for row in document_rows:
            page_key = (row.source_file, row.page)
            if page_key in seen_pages:
                continue
            seen_pages.add(page_key)
            try:
                tax += mod.money(row.page_tax or "0")
            except ValueError:
                pass
        if tax:
            grouped[tax_account] += tax
        document_total = sum(grouped.values(), Decimal("0.00"))
        grand_total += document_total
        all_invoices = sorted({r.invoice for r in document_rows})

        # Fake MapperApp instance methods via module functions
        def output_values(meta, account, debit, credit, invoice_text, description):
            return {
                "Journal Date": meta["date"],
                "Journal No.": meta["journal"],
                "Account": account,
                "Debit Amount": debit,
                "Credit Amount": credit,
                "Invoice Number(s)": invoice_text,
                "PDF Description": description,
                "Vendor Name": "Denny's Inc",
                "Restaurant #": meta["restaurant"],
                "Location": meta["location"],
                "Blank": "",
            }

        for account, amount in grouped.items():
            invoice_text = ", ".join(sorted(invoices.get(account, set())) or all_invoices)
            descriptions = "; ".join(
                dict.fromkeys(
                    f"{r.invoice} - {r.description}" for r in document_rows if r.account == account
                )
            )
            values = output_values(meta, account, float(amount), None, invoice_text, descriptions)
            output_rows.append([values[column_mapping[c]] for c in CSV_COLUMNS])
        values = output_values(
            meta, credit_account, None, float(document_total),
            ", ".join(all_invoices),
            "; ".join(dict.fromkeys(f"{r.invoice} - {r.description}" for r in document_rows)),
        )
        output_rows.append([values[column_mapping[c]] for c in CSV_COLUMNS])

    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv_mod.writer(fh)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(output_rows)

    bridge.normalize_location_column(out)

    job = ConversionJob(
        tool_name="invoice",
        user_id=user.id,
        output_path=str(out),
        status="completed",
        message=f"Debits = Credits = ${grand_total:,.2f}",
        finished_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    write_audit(db, user, "invoice.export", "conversion_job", job.id, job.message, client_ip(request))

    return FileResponse(str(out), filename=out.name)
