"""Settings: locations and chart-of-accounts mapping."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from webapp.database import get_db
from webapp.models import ChartOfAccountRule, FileRecord, Location, User
from webapp.security import client_ip, require
from webapp.services.audit_service import write_audit
from webapp.templating import templates_ctx as templates

router = APIRouter(prefix="/settings", tags=["settings"])

# Tool scopes shown in the Settings -> Chart of Accounts section, with labels.
COA_TOOL_SCOPES = [
    ("remittance", "Daily Remittance"),
    ("daily_sales", "Daily Sales"),
    ("payroll", "Payroll"),
    ("invoice", "Weekly Denny's Invoice"),
    ("hotel_revenue", "Marietta Hotel Revenue"),
]


@router.get("/locations", response_class=HTMLResponse)
def locations_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("settings.manage")),
):
    locations = db.query(Location).order_by(Location.code).all()
    all_rules = (
        db.query(ChartOfAccountRule)
        .order_by(ChartOfAccountRule.tool_scope, ChartOfAccountRule.source_contains)
        .all()
    )
    # Group rules per tool scope for a grouped UI; keep known scopes ordered
    # first, then any custom scopes that may exist.
    labels = dict(COA_TOOL_SCOPES)
    grouped: list[dict] = []
    for scope, label in COA_TOOL_SCOPES:
        grouped.append(
            {"scope": scope, "label": label, "rules": [r for r in all_rules if r.tool_scope == scope]}
        )
    known = {scope for scope, _ in COA_TOOL_SCOPES}
    for r in all_rules:
        if r.tool_scope not in known:
            existing = next((g for g in grouped if g["scope"] == r.tool_scope), None)
            if existing:
                existing["rules"].append(r)
            else:
                grouped.append({"scope": r.tool_scope, "label": r.tool_scope, "rules": [r]})
                known.add(r.tool_scope)
    return templates.TemplateResponse(
        "settings/locations.html",
        {
            "request": request,
            "user": user,
            "locations": locations,
            "coa_groups": grouped,
            "coa_scopes": COA_TOOL_SCOPES,
        },
    )


@router.post("/locations/create")
def create_location(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("settings.manage")),
    code: str = Form(...),
    name: str = Form(...),
    cash_account: str = Form(""),
    bank_account: str = Form(""),
):
    if db.query(Location).filter_by(code=code.strip()).first():
        return RedirectResponse("/settings/locations?error=exists", status_code=303)
    loc = Location(
        code=code.strip(),
        name=name.strip(),
        cash_account=cash_account.strip(),
        bank_account=bank_account.strip(),
    )
    db.add(loc)
    db.commit()
    write_audit(db, user, "settings.location_create", "location", loc.id, loc.name, client_ip(request))
    return RedirectResponse("/settings/locations", status_code=303)


@router.post("/locations/{loc_id}/update")
def update_location(
    loc_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("settings.manage")),
    name: str = Form(...),
    cash_account: str = Form(""),
    bank_account: str = Form(""),
    is_active: str = Form("1"),
):
    loc = db.query(Location).filter_by(id=loc_id).first()
    if loc:
        loc.name = name.strip()
        loc.cash_account = cash_account.strip()
        loc.bank_account = bank_account.strip()
        loc.is_active = is_active == "1"
        db.commit()
        write_audit(db, user, "settings.location_update", "location", loc_id, loc.name, client_ip(request))
    return RedirectResponse("/settings/locations", status_code=303)


@router.post("/locations/{loc_id}/delete")
def delete_location(
    loc_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("settings.manage")),
):
    loc = db.query(Location).filter_by(id=loc_id).first()
    if loc:
        # Detach any file records that reference this location so the row can be
        # removed without violating the foreign key (their location_name is kept).
        for rec in db.query(FileRecord).filter_by(location_id=loc_id).all():
            rec.location_id = None
        write_audit(db, user, "settings.location_delete", "location", loc_id, loc.name, client_ip(request))
        db.delete(loc)
        db.commit()
    return RedirectResponse("/settings/locations", status_code=303)


@router.post("/coa/create")
def create_coa(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("settings.manage")),
    source_contains: str = Form(...),
    account: str = Form(...),
    output_description: str = Form(""),
    entry_type: str = Form("DEBIT"),
    tool_scope: str = Form("remittance"),
):
    rule = ChartOfAccountRule(
        source_contains=source_contains.strip(),
        account=account.strip(),
        output_description=output_description.strip() or source_contains.strip(),
        entry_type=entry_type.strip().upper(),
        tool_scope=tool_scope.strip(),
    )
    db.add(rule)
    db.commit()
    write_audit(db, user, "settings.coa_create", "coa_rule", rule.id, rule.source_contains, client_ip(request))
    return RedirectResponse("/settings/locations", status_code=303)


@router.post("/coa/{rule_id}/delete")
def delete_coa(
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("settings.manage")),
):
    rule = db.query(ChartOfAccountRule).filter_by(id=rule_id).first()
    if rule:
        write_audit(db, user, "settings.coa_delete", "coa_rule", rule_id, rule.source_contains, client_ip(request))
        db.delete(rule)
        db.commit()
    return RedirectResponse("/settings/locations", status_code=303)
