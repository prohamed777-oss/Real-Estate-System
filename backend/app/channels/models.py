"""Channel accounts & raw webhook persistence (§7, §49)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class MessageTemplate(Base, UUIDPk, Timestamped):
    """WhatsApp business-initiated messages require approved templates (Meta).

    body uses {{variable}} placeholders. status mirrors the Meta review
    lifecycle; local 'approved' templates are usable immediately.
    """

    __tablename__ = "message_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "language", name="uq_templates_name_lang"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(10), default="ar")
    category: Mapped[str] = mapped_column(String(40), default="UTILITY")  # MARKETING|UTILITY|AUTHENTICATION
    body: Mapped[str] = mapped_column(Text)  # "مرحبًا {{name}}، وحدة {{unit}} متاحة..."
    variables: Mapped[list] = mapped_column(JSONB, default=list)  # ["name", "unit"]
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|approved|rejected|paused
    external_id: Mapped[str | None] = mapped_column(String(200))  # Meta template id
    created_by: Mapped[str | None] = mapped_column(String(64))


class ChannelAccount(Base, UUIDPk, Timestamped):
    """Per-tenant provider connection (credentials shell — real creds wired at
    integration time by the owner)."""

    __tablename__ = "channel_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "channel", "provider", name="uq_channel_account"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    channel: Mapped[str] = mapped_column(String(30))
    provider: Mapped[str] = mapped_column(String(50))
    display_name: Mapped[str | None] = mapped_column(String(200))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="disconnected")  # connected|disconnected|error
    health: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class WebhookEvent(Base, UUIDPk):
    """Raw provider payloads — persisted BEFORE processing (§49)."""

    __tablename__ = "webhook_events"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    channel: Mapped[str] = mapped_column(String(30))
    external_signature: Mapped[str | None] = mapped_column(String(255))
    fingerprint: Mapped[str | None] = mapped_column(String(128), index=True)  # dedup
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="received")  # received|processed|failed|duplicate
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
