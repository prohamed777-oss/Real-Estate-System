"""Sales domain models (§26-35): opportunities, viewings, offers, reservations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class Opportunity(Base, UUIDPk, Timestamped):
    """Lead ≠ Opportunity (§26): a real commercial chance tied to property interest."""

    __tablename__ = "opportunities"
    __table_args__ = (Index("ix_opps_stage", "tenant_id", "stage"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id"), index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"))
    stage: Mapped[str] = mapped_column(String(30), default="DISCOVERY")
    property_interest: Mapped[list] = mapped_column(JSONB, default=list)  # [asset_id...]
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    probability: Mapped[int] = mapped_column(Integer, default=20)
    next_action: Mapped[str | None] = mapped_column(String(40))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lost_reason: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Viewing(Base, UUIDPk, Timestamped):
    __tablename__ = "viewings"
    __table_args__ = (Index("ix_viewings_schedule", "tenant_id", "scheduled_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("opportunities.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("property_assets.id"), index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id"))
    salesperson_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # UTC (review rule)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    display_timezone: Mapped[str] = mapped_column(String(60), default="Africa/Cairo")
    location: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="REQUESTED")
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    feedback: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(64))


class ViewingEvent(Base, UUIDPk):
    """Reschedule history — never silently rewrite the timeline (§31, §69)."""

    __tablename__ = "viewing_events"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    viewing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("viewings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # rescheduled|cancelled|no_show|confirmed|completed
    from_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    to_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Offer(Base, UUIDPk, Timestamped):
    """Offers are versioned (§33-34): counters = new row with parent_offer_id.
    Price/payment snapshots are IMMUTABLE (§102)."""

    __tablename__ = "offers"
    __table_args__ = (Index("ix_offers_opportunity", "tenant_id", "opportunity_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("opportunities.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("property_assets.id"), index=True)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    parent_offer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, default=1)

    status: Mapped[str] = mapped_column(String(30), default="DRAFT")

    # --- immutable snapshots (§20, §102) ---
    price_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    price_currency: Mapped[str] = mapped_column(String(3), default="EGP")
    payment_plan_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    price_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    discount: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    validity_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(64))
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class NegotiationEntry(Base, UUIDPk):
    __tablename__ = "negotiation_entries"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offers.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # offer|counter|objection|note
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    actor_type: Mapped[str] = mapped_column(String(20), default="user")
    actor_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Reservation(Base, UUIDPk, Timestamped):
    """Transactional commercial operation (§35): idempotent, audited, time-bound."""

    __tablename__ = "reservations"
    __table_args__ = (
        Index("ix_reservations_asset", "tenant_id", "asset_id"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_reservation_idem"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("opportunities.id"), index=True)
    offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offers.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("property_assets.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deposit_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    deposit_currency: Mapped[str] = mapped_column(String(3), default="EGP")
    offer_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(String(64))
    cancelled_reason: Mapped[str | None] = mapped_column(Text)
    converted_contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
