"""Dynamic RBAC: role grants + time-bound per-user overrides."""
from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from webapp.models import Permission, RolePermission, User, UserPermissionOverride

_cache_version = 0
_cache_lock = Lock()
_perm_cache: dict[tuple[int, int], set[str]] = {}


def invalidate_permission_cache() -> None:
    """Bump cache version so the next resolve reloads from DB."""
    global _cache_version, _perm_cache
    with _cache_lock:
        _cache_version += 1
        _perm_cache.clear()


def _is_override_active(override: UserPermissionOverride, now: datetime) -> bool:
    if override.starts_at and now < override.starts_at:
        return False
    if override.expires_at and now >= override.expires_at:
        return False
    return True


def purge_expired_overrides(db: Session, now: Optional[datetime] = None) -> int:
    """Lazily delete expired overrides. Returns count deleted."""
    now = now or datetime.utcnow()
    expired = (
        db.query(UserPermissionOverride)
        .filter(
            UserPermissionOverride.expires_at.isnot(None),
            UserPermissionOverride.expires_at <= now,
        )
        .all()
    )
    count = len(expired)
    for row in expired:
        db.delete(row)
    if count:
        db.commit()
        invalidate_permission_cache()
    return count


def effective_permissions(db: Session, user: User) -> set[str]:
    """Resolve effective permission codes for a user (role + overrides)."""
    global _cache_version
    with _cache_lock:
        key = (user.id, _cache_version)
        if key in _perm_cache:
            return set(_perm_cache[key])

    now = datetime.utcnow()
    # Role grants
    granted: set[str] = set()
    denied: set[str] = set()

    role_perms = (
        db.query(RolePermission)
        .options(joinedload(RolePermission.permission))
        .filter(RolePermission.role_id == user.role_id)
        .all()
    )
    for rp in role_perms:
        if rp.allowed:
            granted.add(rp.permission.code)
        else:
            denied.add(rp.permission.code)

    overrides = (
        db.query(UserPermissionOverride)
        .options(joinedload(UserPermissionOverride.permission))
        .filter(UserPermissionOverride.user_id == user.id)
        .all()
    )
    # Purge expired among loaded rows without always scanning whole table
    to_delete = []
    for ov in overrides:
        if ov.expires_at and ov.expires_at <= now:
            to_delete.append(ov)
            continue
        if not _is_override_active(ov, now):
            continue
        code = ov.permission.code
        if ov.allowed:
            granted.add(code)
            denied.discard(code)
        else:
            denied.add(code)
            granted.discard(code)

    if to_delete:
        for row in to_delete:
            db.delete(row)
        db.commit()

    result = granted - denied
    with _cache_lock:
        _perm_cache[(user.id, _cache_version)] = set(result)
    return result


def has_permission(db: Session, user: User, permission_code: str) -> bool:
    if not user or not user.is_active:
        return False
    return permission_code in effective_permissions(db, user)


def permission_status_for_user(
    db: Session, user: User
) -> list[dict]:
    """Return Inherited/Granted/Denied status for every permission (for UI)."""
    now = datetime.utcnow()
    all_perms = db.query(Permission).order_by(Permission.category, Permission.code).all()
    role_map = {
        rp.permission_id: rp.allowed
        for rp in db.query(RolePermission).filter(RolePermission.role_id == user.role_id)
    }
    override_map = {
        ov.permission_id: ov
        for ov in db.query(UserPermissionOverride)
        .filter(UserPermissionOverride.user_id == user.id)
        .all()
        if _is_override_active(ov, now)
    }

    rows = []
    for perm in all_perms:
        inherited = role_map.get(perm.id, False)
        ov = override_map.get(perm.id)
        if ov is not None:
            status = "Granted" if ov.allowed else "Denied"
            temporary = bool(ov.expires_at)
            expires_at = ov.expires_at
            reason = ov.reason
            override_id = ov.id
        else:
            status = "Inherited" if inherited else "None"
            temporary = False
            expires_at = None
            reason = ""
            override_id = None
        effective = (
            ov.allowed if ov is not None else inherited
        )
        rows.append(
            {
                "permission": perm,
                "inherited": inherited,
                "status": status,
                "effective": effective,
                "temporary": temporary,
                "expires_at": expires_at,
                "reason": reason,
                "override_id": override_id,
            }
        )
    return rows
