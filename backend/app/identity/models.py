"""Identity & Customer domain (§4-5): Person vs channel identities."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk

CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_PHONE = "phone"
CHANNEL_EMAIL = "email"
CHANNEL_INSTAGRAM = "instagram"
CHANNEL_FACEBOOK = "facebook"
CHANNEL_WEB = "web"
CHANNELS = {CHANNEL_WHATSAPP, CHANNEL_PHONE, CHANNEL_EMAIL, CHANNEL_INSTAGRAM, CHANNEL_FACEBOOK, CHANNEL_WEB}


class Person(Base, UUIDPk, Timestamped):
    """The human being — NOT any single channel identity (§4)."""

    __tablename__ = "people"
    __table_args__ = (Index("ix_people_name", "full_name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    type: Mapped[str] = mapped_column(String(30), default="customer")  # customer|owner|developer_contact|partner
    full_name: Mapped[str] = mapped_column(String(300), default="")
    full_name_alt: Mapped[str | None] = mapped_column(String(300))  # secondary language
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(30), index=True)  # E.164
    company_name: Mapped[str | None] = mapped_column(String(200))
    locale: Mapped[str] = mapped_column(String(10), default="ar")
    timezone: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|archived|merged
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str | None] = mapped_column(String(50))
    external_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Identity(Base, UUIDPk):
    """One person, many channel handles — resolution target for inbound messages."""

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("tenant_id", "channel", "external_id", name="uq_identities_channel"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id"), index=True)
    channel: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(200))  # phone/wa jid/email/social id
    display_name: Mapped[str | None] = mapped_column(String(200))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class CommunicationConsent(Base, UUIDPk):
    """Consent & preferences gate for ALL outbound (§5)."""

    __tablename__ = "communication_consents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "person_id", "channel", "consent_type", name="uq_consent_scope"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id"), index=True)
    channel: Mapped[str] = mapped_column(String(30))
    consent_type: Mapped[str] = mapped_column(String(30), default="marketing")  # marketing|transactional
    status: Mapped[str] = mapped_column(String(20), default="opt_in")  # opt_in | opt_out
    source: Mapped[str | None] = mapped_column(String(100))
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {start:"22:00", end:"08:00", tz:"Africa/Cairo"}
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class CustomerProfile(Base, UUIDPk, Timestamped):
    """Stable, structured customer facts — not AI memory (§57)."""

    __tablename__ = "customer_profiles"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id"), unique=True, index=True)
    preferred_channel: Mapped[str | None] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(10), default="ar")
    explicit_preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    behavioral_preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    lifetime_stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
