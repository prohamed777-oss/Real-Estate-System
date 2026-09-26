"""Capability Gateway + Custom Fields persistence (V4 PART 10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class CapabilityGrant(Base, UUIDPk):
    """One grant per (grantee, resource, action). Every grant EXPIRES."""

    __tablename__ = "capability_grants"
    __table_args__ = (
        Index("ix_cap_grants_lookup", "tenant_id", "grantee_type", "grantee_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    grantee_type: Mapped[str] = mapped_column(String(30))  # AI_AGENT|OUTBOUND_WEBHOOK|MARKETPLACE_APP|API_CLIENT
    grantee_id: Mapped[str] = mapped_column(String(200))
    resource_scope: Mapped[str] = mapped_column(String(100))  # "leads" | "*" ...
    action_scope: Mapped[str] = mapped_column(String(30))     # "read" | "write" | "*" ...
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[str | None] = mapped_column(String(120))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookDestination(Base, UUIDPk):
    """Outbound webhook allowlist — SSRF defense (V4 10.3)."""

    __tablename__ = "webhook_destinations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "url", name="uq_webhook_dest_url"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    allowlist_status: Mapped[str] = mapped_column(String(30), default="PENDING_VERIFICATION")
    verification_token: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CustomFieldDefinition(Base, UUIDPk, Timestamped):
    """Custom fields live in a SEPARATE registry — collision with base columns
    is impossible BY STRUCTURE, not by convention (V4 10.5)."""

    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entity_type", "field_key", name="uq_cfd_key"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    field_key: Mapped[str] = mapped_column(String(100))
    field_type: Mapped[str] = mapped_column(String(30), default="text")  # text|number|date|select|multiselect|boolean
    label: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {ar, en}
    options: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[str | None] = mapped_column(String(64))


class CustomFieldValue(Base):
    """EAV side table. No base-table migrations, no schema drift across tenants."""

    __tablename__ = "custom_field_values"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entity_type", "entity_id", "field_key",
                          name="uq_cfv_key"),
        Index("ix_cfv_entity", "tenant_id", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    field_key: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {"v": ...}
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
