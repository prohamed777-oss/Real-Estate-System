"""Listing domain (§21-22): PropertyAsset ≠ Listing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class Listing(Base, UUIDPk, Timestamped):
    __tablename__ = "listings"
    __table_args__ = (Index("ix_listings_asset", "tenant_id", "asset_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("property_assets.id"), index=True)
    title: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)      # {ar, en} i18n
    description: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {ar, en}
    asking_price_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    asking_price_currency: Mapped[str] = mapped_column(String(3), default="EGP")
    price_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payment_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    marketing: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    media: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(30), default="draft")  # draft|active|paused|expired|archived
    created_by: Mapped[str | None] = mapped_column(String(64))


class ListingChannel(Base, UUIDPk, Timestamped):
    """Syndication per channel (§22) with its own lifecycle."""

    __tablename__ = "listing_channels"
    __table_args__ = (Index("ix_listing_channels_listing", "tenant_id", "listing_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id"), index=True)
    channel: Mapped[str] = mapped_column(String(50))  # website|social|portal|partner|internal
    status: Mapped[str] = mapped_column(String(30), default="draft")
    external_id: Mapped[str | None] = mapped_column(String(200))
    external_url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
