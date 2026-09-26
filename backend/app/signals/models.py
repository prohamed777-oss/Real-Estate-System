"""V4 data-plane models: Claims, Signal Engine, Projection Registry (staleness)."""

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
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class SignalDefinition(Base, UUIDPk, Timestamped):
    """One definition, one transform — online and offline MUST agree (1.32)."""

    __tablename__ = "signal_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "definition_version",
                          name="uq_signal_def_version"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    definition_version: Mapped[str] = mapped_column(String(30), default="v1")
    transform_sql: Mapped[str | None] = mapped_column(Text)  # the SINGLE source of computation
    description: Mapped[str | None] = mapped_column(Text)
    online_ttl_seconds: Mapped[int] = mapped_column(Integer, default=300)


class SignalValue(Base):
    """Materialized (online) signal values with freshness metadata."""

    __tablename__ = "signal_values"
    __table_args__ = (
        Index("ix_signals_lookup", "tenant_id", "entity_type", "entity_id", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(120))
    value: Mapped[float | None] = mapped_column(Numeric(18, 6))
    definition_version: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(30), default="online")  # online|offline
    freshness: Mapped[str] = mapped_column(String(20), default="fresh")  # fresh|stale
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectionRegistry(Base, UUIDPk):
    """Staleness contracts (V4 4.7, Principle 1.26): every projection declares
    how stale it may get, and the system tracks actual lag."""

    __tablename__ = "projection_registry"
    __table_args__ = (
        UniqueConstraint("tenant_id", "projection_name", "environment",
                          name="uq_projection_registry"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    projection_name: Mapped[str] = mapped_column(String(120))  # property_search_documents...
    environment: Mapped[str] = mapped_column(String(20), default="production")
    projection_type: Mapped[str] = mapped_column(String(40), default="read_model")
    max_staleness_ms: Mapped[int] = mapped_column(Integer, default=60000)
    consumer_cursor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_lag_ms: Mapped[int] = mapped_column(Integer, default=0)
    critical_path: Mapped[bool] = mapped_column(Boolean, default=False)  # transaction-critical → never trusted
    degraded_mode: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


