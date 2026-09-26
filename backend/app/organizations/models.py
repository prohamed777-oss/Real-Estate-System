"""Platform Core models: tenants, organizations, RBAC, audit, idempotency (§3, §93, §94)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class Tenant(Base, UUIDPk, Timestamped):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|suspended|archived
    plan: Mapped[str] = mapped_column(String(50), default="trial")
    locale: Mapped[str] = mapped_column(String(10), default="ar")  # ar | en
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    timezone: Mapped[str] = mapped_column(String(60), default="Africa/Cairo")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Organization(Base, UUIDPk, Timestamped):
    __tablename__ = "organizations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str | None] = mapped_column(String(300))
    timezone: Mapped[str] = mapped_column(String(60), default="Africa/Cairo")
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    locale: Mapped[str] = mapped_column(String(10), default="ar")
    status: Mapped[str] = mapped_column(String(20), default="active")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Branch(Base, UUIDPk, Timestamped):
    __tablename__ = "branches"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(50))
    timezone: Mapped[str] = mapped_column(String(60), default="Africa/Cairo")
    address: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Team(Base, UUIDPk, Timestamped):
    __tablename__ = "teams"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"))
    name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(50), default="sales")


class User(Base, UUIDPk):
    """Mirrors a Supabase auth user (id == auth.users.id)."""

    __tablename__ = "users"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str | None] = mapped_column(String(30))
    locale: Mapped[str] = mapped_column(String(10), default="ar")
    timezone: Mapped[str] = mapped_column(String(60), default="Africa/Cairo")
    avatar_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Role(Base, UUIDPk, Timestamped):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_roles_tenant_key"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id"), index=True
    )  # NULL = system role template
    key: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(100), primary_key=True)


class Membership(Base, UUIDPk, Timestamped):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_memberships_user_org"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"))
    custom_permissions: Mapped[list] = mapped_column(JSONB, default=list)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base, UUIDPk):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_tenant_time", "tenant_id", "occurred_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor_type: Mapped[str] = mapped_column(String(20))  # user | system | agent
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(120))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(30), default="api")
    request_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class IdempotencyKey(Base, UUIDPk):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_idem_tenant_key"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="in_flight")
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeatureFlag(Base, UUIDPk, Timestamped):
    __tablename__ = "feature_flags"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_flags_tenant_key"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    rollout: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
