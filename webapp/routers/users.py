"""User management (Admin)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from webapp.database import get_db
from webapp.models import Role, User, UserPermissionOverride, Permission
from webapp.policy import invalidate_permission_cache, permission_status_for_user
from webapp.security import client_ip, hash_password, require
from webapp.services.audit_service import write_audit
from webapp.templating import templates_ctx as templates

router = APIRouter(prefix="/users", tags=["users"])


def _count_active_admins(db: Session) -> int:
    admin_role = db.query(Role).filter_by(name="Admin").first()
    if not admin_role:
        return 0
    return (
        db.query(User)
        .filter(User.role_id == admin_role.id, User.is_active.is_(True))
        .count()
    )


@router.get("", response_class=HTMLResponse)
def list_users(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("users.manage")),
):
    users = db.query(User).options(joinedload(User.role)).order_by(User.username).all()
    roles = db.query(Role).order_by(Role.name).all()
    return templates.TemplateResponse(
        "users/list.html",
        {"request": request, "user": user, "users": users, "roles": roles},
    )


@router.post("/create")
def create_user(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("users.manage")),
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role_id: int = Form(...),
):
    if db.query(User).filter(
        (User.username == username.strip()) | (User.email == email.strip())
    ).first():
        return RedirectResponse("/users?error=exists", status_code=303)
    new_user = User(
        username=username.strip(),
        email=email.strip(),
        password_hash=hash_password(password),
        role_id=role_id,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    write_audit(db, user, "users.create", "user", new_user.id, username, client_ip(request))
    invalidate_permission_cache()
    return RedirectResponse("/users", status_code=303)


@router.get("/{user_id}", response_class=HTMLResponse)
def user_detail(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("users.manage")),
):
    target = (
        db.query(User).options(joinedload(User.role)).filter_by(id=user_id).first()
    )
    if not target:
        return RedirectResponse("/users", status_code=303)
    roles = db.query(Role).order_by(Role.name).all()
    perm_rows = permission_status_for_user(db, target)
    overrides = (
        db.query(UserPermissionOverride)
        .options(joinedload(UserPermissionOverride.permission))
        .filter_by(user_id=user_id)
        .order_by(UserPermissionOverride.created_at.desc())
        .all()
    )
    permissions = db.query(Permission).order_by(Permission.category, Permission.code).all()
    return templates.TemplateResponse(
        "users/detail.html",
        {
            "request": request,
            "user": user,
            "target": target,
            "roles": roles,
            "perm_rows": perm_rows,
            "overrides": overrides,
            "permissions": permissions,
            "now": datetime.utcnow(),
        },
    )


@router.post("/{user_id}/role")
def change_role(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("users.manage")),
    role_id: int = Form(...),
):
    target = db.query(User).options(joinedload(User.role)).filter_by(id=user_id).first()
    if not target:
        return RedirectResponse("/users", status_code=303)
    admin_role = db.query(Role).filter_by(name="Admin").first()
    new_role = db.query(Role).filter_by(id=role_id).first()
    if (
        admin_role
        and target.role_id == admin_role.id
        and new_role
        and new_role.id != admin_role.id
        and _count_active_admins(db) <= 1
    ):
        return RedirectResponse(f"/users/{user_id}?error=last_admin", status_code=303)
    old = target.role.name if target.role else ""
    target.role_id = role_id
    db.commit()
    invalidate_permission_cache()
    write_audit(
        db, user, "users.role_change", "user", user_id,
        f"{old} -> {new_role.name if new_role else role_id}", client_ip(request),
    )
    return RedirectResponse(f"/users/{user_id}", status_code=303)


@router.post("/{user_id}/toggle-active")
def toggle_active(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("users.manage")),
):
    target = db.query(User).options(joinedload(User.role)).filter_by(id=user_id).first()
    if not target:
        return RedirectResponse("/users", status_code=303)
    if target.is_active and target.role and target.role.name == "Admin":
        if _count_active_admins(db) <= 1:
            return RedirectResponse(f"/users/{user_id}?error=last_admin", status_code=303)
    target.is_active = not target.is_active
    db.commit()
    invalidate_permission_cache()
    write_audit(
        db, user, "users.toggle_active", "user", user_id,
        f"active={target.is_active}", client_ip(request),
    )
    return RedirectResponse(f"/users/{user_id}", status_code=303)


@router.post("/{user_id}/password")
def reset_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("users.manage")),
    password: str = Form(...),
):
    target = db.query(User).filter_by(id=user_id).first()
    if target:
        target.password_hash = hash_password(password)
        db.commit()
        write_audit(db, user, "users.password_reset", "user", user_id, "", client_ip(request))
    return RedirectResponse(f"/users/{user_id}", status_code=303)


@router.post("/{user_id}/overrides")
def set_override(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("roles.manage")),
    permission_id: int = Form(...),
    allowed: str = Form("grant"),
    starts_at: str = Form(""),
    expires_at: str = Form(""),
    reason: str = Form(""),
):
    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        return RedirectResponse("/users", status_code=303)

    def parse_dt(value: str) -> Optional[datetime]:
        value = (value or "").strip()
        if not value:
            return None
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None

    ov = UserPermissionOverride(
        user_id=user_id,
        permission_id=permission_id,
        allowed=(allowed == "grant"),
        starts_at=parse_dt(starts_at),
        expires_at=parse_dt(expires_at),
        granted_by=user.id,
        reason=reason.strip(),
    )
    db.add(ov)
    db.commit()
    invalidate_permission_cache()
    perm = db.query(Permission).filter_by(id=permission_id).first()
    write_audit(
        db, user, "users.override", "user", user_id,
        f"{'grant' if ov.allowed else 'deny'} {perm.code if perm else permission_id} "
        f"until {ov.expires_at or 'open'} reason={reason}",
        client_ip(request),
    )
    return RedirectResponse(f"/users/{user_id}", status_code=303)


@router.post("/{user_id}/overrides/{override_id}/delete")
def delete_override(
    user_id: int,
    override_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("roles.manage")),
):
    ov = db.query(UserPermissionOverride).filter_by(id=override_id, user_id=user_id).first()
    if ov:
        write_audit(
            db, user, "users.override_delete", "user", user_id,
            str(override_id), client_ip(request),
        )
        db.delete(ov)
        db.commit()
        invalidate_permission_cache()
    return RedirectResponse(f"/users/{user_id}", status_code=303)
