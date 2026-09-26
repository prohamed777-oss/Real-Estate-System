"""Marketing & Acquisition models (§40-41)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class Campaign(Base, UUIDPk, Timestamped):
    __tablename__ = "campaigns"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str | None] = mapped_column(String(50))  # meta_ads|google_ads|tiktok|offline
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|active|paused|ended
    budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    spend: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    utm: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {utm_source, utm_medium, utm_campaign}
    landing_page_url: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LeadSource(Base, UUIDPk, Timestamped):
    __tablename__ = "lead_sources"
    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint("tenant_id", "key", name="uq_lead_sources_key"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str | None] = mapped_column(String(50))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id"))
    is_active: Mapped[bool] = mapped_column(default=True)


class Attribution(Base, UUIDPk, Timestamped):
    """Lead attribution (§41): first/last touch → revenue linkage."""

    __tablename__ = "attributions"
    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint("tenant_id", "lead_id",
                                                   name="uq_attributions_lead"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    first_touch: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_touch: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source: Mapped[str | None] = mapped_column(String(100))
    medium: Mapped[str | None] = mapped_column(String(100))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id"))
    content: Mapped[str | None] = mapped_column(String(200))
    landing_page: Mapped[str | None] = mapped_column(Text)
    referrer: Mapped[str | None] = mapped_column(Text)
