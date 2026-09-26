"""Marketing content engine models (§42): generate → review → approve → publish."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class MarketingAsset(Base, UUIDPk, Timestamped):
    __tablename__ = "marketing_assets"
    __table_args__ = (Index("ix_marketing_assets_status", "tenant_id", "status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))  # listing_title|listing_description|whatsapp_copy|instagram_caption|ad_copy|email_copy|video_script
    language: Mapped[str] = mapped_column(String(10), default="ar")
    brief: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|approved|rejected|published
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(80))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
