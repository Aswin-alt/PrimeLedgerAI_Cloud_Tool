"""Password hashing and session-backed authentication."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy.orm import Session, joinedload

from webapp.database import get_db
from webapp.models import User
from webapp.policy import has_permission

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return (
        db.query(User)
        .options(joinedload(User.role))
        .filter(User.id == user_id, User.is_active.is_(True))
        .first()
    )


def require_login(
    request: Request, db: Session = Depends(get_db)
) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user


def require(permission_code: str):
    """FastAPI dependency factory enforcing a live DB permission check."""

    def _dependency(
        request: Request, db: Session = Depends(get_db)
    ) -> User:
        user = get_current_user(request, db)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/login"},
            )
        if not has_permission(db, user, permission_code):
            from webapp.services.audit_service import write_audit

            write_audit(
                db,
                user,
                "permission.denied",
                "permission",
                permission_code,
                f"Denied {permission_code}",
                request.client.host if request.client else "",
            )
            raise HTTPException(status_code=403, detail="Permission denied")
        return user

    return _dependency


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""
