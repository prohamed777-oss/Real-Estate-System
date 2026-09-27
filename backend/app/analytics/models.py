"""Analytics/billing models (§99): usage metering for tenant billing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk


class UsageMeter(Base, UUIDPk):
    """Usage metering per tenant: ai_requests, messages, voice_minutes, storage, automation_runs."""

    __tablename__ = "usage_meters"
    __table_args__ = (Index("ix_usage_tenant_kind_time", "tenant_id", "kind", "occurred_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))  # ai_request|message|voice_minute|storage_mb|automation_run
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class Subscription(Base, UUIDPk):
    __tablename__ = "subscriptions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(50), default="trial")
    seats: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[str] = mapped_column(String(30), default="active")
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
