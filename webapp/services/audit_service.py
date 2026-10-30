"""Audit log writer used by all mutating routes."""
from __future__ import annotations

from typing import Optional, Union

from sqlalchemy.orm import Session

from webapp.models import AuditLog, User


def write_audit(
    db: Session,
    user: Optional[User],
    action: str,
    entity_type: str = "",
    entity_id: Union[str, int, None] = "",
    detail: str = "",
    ip_address: str = "",
) -> AuditLog:
    entry = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type or "",
        entity_id=str(entity_id) if entity_id is not None else "",
        detail=detail or "",
        ip_address=ip_address or "",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
