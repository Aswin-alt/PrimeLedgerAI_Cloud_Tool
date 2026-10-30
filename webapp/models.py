"""ORM models for PrimeLedgerAI Cloud."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from webapp.database import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="role")
    role_permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="General")

    role_permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="permission", cascade="all, delete-orphan"
    )
    overrides: Mapped[list["UserPermissionOverride"]] = relationship(
        back_populates="permission", cascade="all, delete-orphan"
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"))
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)

    role: Mapped["Role"] = relationship(back_populates="role_permissions")
    permission: Mapped["Permission"] = relationship(back_populates="role_permissions")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    role: Mapped["Role"] = relationship(back_populates="users")
    overrides: Mapped[list["UserPermissionOverride"]] = relationship(
        back_populates="user",
        foreign_keys="UserPermissionOverride.user_id",
        cascade="all, delete-orphan",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="user")
    file_records: Mapped[list["FileRecord"]] = relationship(back_populates="uploader")
    conversion_jobs: Mapped[list["ConversionJob"]] = relationship(back_populates="user")


class UserPermissionOverride(Base):
    __tablename__ = "user_permission_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"))
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    granted_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(
        back_populates="overrides", foreign_keys=[user_id]
    )
    permission: Mapped["Permission"] = relationship(back_populates="overrides")
    granter: Mapped[Optional["User"]] = relationship(foreign_keys=[granted_by])


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    cash_account: Mapped[str] = mapped_column(String(128), default="")
    bank_account: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    file_records: Mapped[list["FileRecord"]] = relationship(back_populates="location")


class ChartOfAccountRule(Base):
    __tablename__ = "chart_of_account_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_contains: Mapped[str] = mapped_column(String(128), nullable=False)
    account: Mapped[str] = mapped_column(String(255), nullable=False)
    output_description: Mapped[str] = mapped_column(String(255), default="")
    entry_type: Mapped[str] = mapped_column(String(16), default="DEBIT")
    tool_scope: Mapped[str] = mapped_column(String(64), default="remittance")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FileRecord(Base):
    __tablename__ = "file_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_type: Mapped[str] = mapped_column(String(64), nullable=False)
    work_date: Mapped[str] = mapped_column(String(32), default="")
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("locations.id"), nullable=True
    )
    location_name: Mapped[str] = mapped_column(String(128), default="All")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="Received")
    discrepancy: Mapped[str] = mapped_column(String(32), default="0.00")
    follow_up: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    location: Mapped[Optional["Location"]] = relationship(back_populates="file_records")
    uploader: Mapped[Optional["User"]] = relationship(back_populates="file_records")
    jobs: Mapped[list["ConversionJob"]] = relationship(back_populates="file_record")


class ConversionJob(Base):
    __tablename__ = "conversion_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    file_record_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("file_records.id"), nullable=True
    )
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    input_path: Mapped[str] = mapped_column(String(512), default="")
    output_path: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    discrepancy: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    file_record: Mapped[Optional["FileRecord"]] = relationship(back_populates="jobs")
    user: Mapped[Optional["User"]] = relationship(back_populates="conversion_jobs")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[Optional["User"]] = relationship(back_populates="audit_logs")


class ApiToken(Base):
    """Optional API tokens for future integrations (Phase 2)."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TallyRun(Base):
    """One Tally report run. The uploaded CSV is re-parsed on every recompute,
    so no parsed state is held in process memory."""

    __tablename__ = "tally_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    company: Mapped[str] = mapped_column(String(255), default="")
    input_path: Mapped[str] = mapped_column(String(512), default="")
    file_name: Mapped[str] = mapped_column(String(255), default="")
    date_from: Mapped[str] = mapped_column(String(32), default="")
    date_to: Mapped[str] = mapped_column(String(32), default="")
    locations: Mapped[str] = mapped_column(Text, default="")
    accounts: Mapped[str] = mapped_column(Text, default="")
    prefix: Mapped[str] = mapped_column(String(16), default="DS")
    receipt_type: Mapped[str] = mapped_column(String(32), default="Deposit")
    status: Mapped[str] = mapped_column(String(32), default="completed")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[Optional["User"]] = relationship("User")


class TallyOpeningBalance(Base):
    """Opening balances for the Credit Sales report, keyed exactly as the
    desktop JSON store was: company + as-of date + location + account.

    No row means the opening balance is unknown. It is never treated as zero,
    so clearing the field deletes the row rather than storing 0.00.
    """

    __tablename__ = "tally_opening_balances"
    __table_args__ = (
        UniqueConstraint("company", "as_of", "location", "account", name="uq_tally_opening"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    as_of: Mapped[str] = mapped_column(String(32), nullable=False)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    account: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
