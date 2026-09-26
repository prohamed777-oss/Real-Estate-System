"""Durable Event History (V4 4.1-4.2): append-only, replayable, causal.

domain_events is NOT a queue (that's outbox_events). It is the permanent
record of what happened, with the enriched envelope: actor, correlation,
causation, aggregate_version, producer. Replay/forensics/projection-rebuild
read from here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk


class DomainEventHistory(Base, UUIDPk):
    __tablename__ = "domain_events"
    __table_args__ = (Index("ix_deh_agg", "tenant_id", "aggregate_type", "aggregate_id"),)

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4, unique=True)
    event_type: Mapped[str] = mapped_column(String(150))
    event_version: Mapped[int] = mapped_column(default=1)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    aggregate_type: Mapped[str | None] = mapped_column(String(100))
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    aggregate_version: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    actor_type: Mapped[str | None] = mapped_column(String(20))  # user|system|agent|automation
    actor_id: Mapped[str | None] = mapped_column(String(120))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    causation_id: Mapped[str | None] = mapped_column(String(64))
    producer: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


async def record_history(
    session, *, event_name: str, tenant_id: uuid.UUID | None, payload: dict[str, Any],
    aggregate_type: str | None = None, aggregate_id: uuid.UUID | None = None,
    aggregate_version: int | None = None, actor_type: str | None = None,
    actor_id: str | None = None, correlation_id: str | None = None,
    causation_id: str | None = None, producer: str = "revenue-os",
    metadata: dict[str, Any] | None = None, occurred_at=None,
) -> uuid.UUID:  # noqa: ANN001
    """Called by emit() — history and transport queue are written together."""
    row = DomainEventHistory(
        event_type=event_name, tenant_id=tenant_id, payload=payload,
        aggregate_type=aggregate_type, aggregate_id=aggregate_id,
        aggregate_version=aggregate_version, actor_type=actor_type,
        actor_id=actor_id, correlation_id=correlation_id,
        causation_id=causation_id, producer=producer, metadata_=metadata or {},
        occurred_at=occurred_at or datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row.event_id
