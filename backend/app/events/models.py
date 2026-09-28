"""Outbox + Job Queue persistence (§47-50, review rule #1).

Separation of concerns:
    DB transaction -> outbox_events row (same tx, never lost)
    dispatcher      -> executes registered event handlers / enqueues jobs
    job queue       -> workers claim with FOR UPDATE SKIP LOCKED + lease
    failure         -> retry w/ backoff -> dead-letter
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, func
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk


class OutboxEvent(Base, UUIDPk):
    __tablename__ = "outbox_events"

    event_name: Mapped[str] = mapped_column(String(150))
    event_version: Mapped[int] = mapped_column(Integer, default=1)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    aggregate_type: Mapped[str | None] = mapped_column(String(100))
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    trace_id: Mapped[str | None] = mapped_column(String(64))
    causation_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_outbox_claim", "status", "next_attempt_at", "created_at"),
    )


class ProcessedEvent(Base):
    """Exactly-once bookkeeping: (consumer_id, event_id) must be unique (§48)."""

    __tablename__ = "processed_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consumer_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Job(Base, UUIDPk):
    __tablename__ = "jobs"

    type: Mapped[str] = mapped_column(String(120), index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    unique_key: Mapped[str | None] = mapped_column(String(200))

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)  # lower runs first
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(120))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))

    __table_args__ = (
        Index("ix_jobs_claim", "status", "priority", "available_at"),
        Index(
            "uq_jobs_dedup",
            "tenant_id",
            "type",
            "unique_key",
            unique=True,
            postgresql_where=sqlalchemy_text("status IN ('pending','running')"),
        ),
    )


class JobLeaseRequeue(Base):
    __tablename__ = "job_lease_requeues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    requeued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    reason: Mapped[str | None] = mapped_column(String(255))


def utcnow() -> datetime:
    return datetime.now(UTC)


def _func_now() -> Any:  # pragma: no cover - helper placeholder
    return func.now()
