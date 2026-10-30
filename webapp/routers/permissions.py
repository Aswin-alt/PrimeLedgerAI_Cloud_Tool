"""Role & permission matrix management (Admin)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from webapp.database import get_db
from webapp.models import Permission, Role, RolePermission, User
from webapp.policy import invalidate_permission_cache
from webapp.security import client_ip, require
from webapp.services.audit_service import write_audit
from webapp.templating import templates_ctx as templates

router = APIRouter(prefix="/permissions", tags=["permissions"])


@router.get("", response_class=HTMLResponse)
def matrix_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("roles.manage")),
):
    roles = db.query(Role).order_by(Role.name).all()
    permissions = db.query(Permission).order_by(Permission.category, Permission.code).all()
    # map (role_id, permission_id) -> allowed
    grants = {
        (rp.role_id, rp.permission_id): rp.allowed
        for rp in db.query(RolePermission).all()
    }
    categories = []
    seen = set()
    for p in permissions:
        if p.category not in seen:
            categories.append(p.category)
            seen.add(p.category)
    return templates.TemplateResponse(
        "permissions/matrix.html",
        {
            "request": request,
            "user": user,
            "roles": roles,
            "permissions": permissions,
            "grants": grants,
            "categories": categories,
        },
    )


@router.post("/toggle")
def toggle_permission(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("roles.manage")),
    role_id: int = Form(...),
    permission_id: int = Form(...),
):
    role = db.query(Role).filter_by(id=role_id).first()
    perm = db.query(Permission).filter_by(id=permission_id).first()
    if not role or not perm:
        return RedirectResponse("/permissions", status_code=303)

    # Guard: cannot strip users.manage from last Admin role if only one admin
    if (
        role.name == "Admin"
        and perm.code == "users.manage"
    ):
        rp = (
            db.query(RolePermission)
            .filter_by(role_id=role_id, permission_id=permission_id)
            .first()
        )
        if rp and rp.allowed:
            admin_users = (
                db.query(User).filter(User.role_id == role.id, User.is_active.is_(True)).count()
            )
            # still allow toggle if other admins somehow have override — keep simple: block remove from Admin role
            return RedirectResponse("/permissions?error=protect_admin", status_code=303)

    rp = (
        db.query(RolePermission)
        .filter_by(role_id=role_id, permission_id=permission_id)
        .first()
    )
    if rp:
        before = rp.allowed
        if rp.allowed:
            db.delete(rp)
            after = False
        else:
            rp.allowed = True
            after = True
    else:
        before = False
        db.add(RolePermission(role_id=role_id, permission_id=permission_id, allowed=True))
        after = True
    db.commit()
    invalidate_permission_cache()
    write_audit(
        db, user, "roles.permission_toggle", "role", role_id,
        f"{role.name}.{perm.code}: {before} -> {after}", client_ip(request),
    )
    # HTMX partial or redirect
    if request.headers.get("HX-Request"):
        checked = "checked" if after else ""
        html = (
            f'<input type="checkbox" {checked} '
            f'hx-post="/permissions/toggle" hx-vals=\'{{"role_id":{role_id},"permission_id":{permission_id}}}\' '
            f'hx-swap="outerHTML" class="h-4 w-4 rounded border-slate-300 text-brand-600" />'
        )
        from fastapi.responses import HTMLResponse as HR
        return HR(html)
    return RedirectResponse("/permissions", status_code=303)


@router.post("/roles/create")
def create_role(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("roles.manage")),
    name: str = Form(...),
    description: str = Form(""),
):
    name = name.strip()
    if not name or db.query(Role).filter_by(name=name).first():
        return RedirectResponse("/permissions?error=role_exists", status_code=303)
    role = Role(name=name, description=description.strip(), is_system=False)
    db.add(role)
    db.commit()
    write_audit(db, user, "roles.create", "role", role.id, name, client_ip(request))
    invalidate_permission_cache()
    return RedirectResponse("/permissions", status_code=303)


@router.post("/roles/{role_id}/rename")
def rename_role(
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("roles.manage")),
    name: str = Form(...),
    description: str = Form(""),
):
    role = db.query(Role).filter_by(id=role_id).first()
    if not role:
        return RedirectResponse("/permissions", status_code=303)
    if role.is_system and role.name in ("Admin",):
        # allow description change only for Admin name
        role.description = description.strip()
    else:
        old = role.name
        role.name = name.strip() or role.name
        role.description = description.strip()
        write_audit(
            db, user, "roles.rename", "role", role_id,
            f"{old} -> {role.name}", client_ip(request),
        )
    db.commit()
    invalidate_permission_cache()
    return RedirectResponse("/permissions", status_code=303)


@router.post("/roles/{role_id}/delete")
def delete_role(
    role_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("roles.manage")),
):
    role = db.query(Role).filter_by(id=role_id).first()
    if not role or role.is_system:
        return RedirectResponse("/permissions?error=system_role", status_code=303)
    if db.query(User).filter_by(role_id=role_id).count():
        return RedirectResponse("/permissions?error=role_in_use", status_code=303)
    write_audit(db, user, "roles.delete", "role", role_id, role.name, client_ip(request))
    db.delete(role)
    db.commit()
    invalidate_permission_cache()
    return RedirectResponse("/permissions", status_code=303)
