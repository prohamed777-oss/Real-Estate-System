"""Automation models (§43-44, §29): rules, durable journeys, SLA, notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class AutomationRule(Base, UUIDPk, Timestamped):
    """Trigger → Conditions → Actions (§43)."""

    __tablename__ = "automation_rules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    trigger_event: Mapped[str] = mapped_column(String(150), index=True)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    actions: Mapped[list] = mapped_column(JSONB, default=list)  # [{type, params}]
    is_active: Mapped[bool] = mapped_column(default=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Journey(Base, UUIDPk, Timestamped):
    """Durable multi-step journey definition (§44)."""

    __tablename__ = "journeys"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    trigger_event: Mapped[str | None] = mapped_column(String(150))
    definition: Mapped[list] = mapped_column(JSONB, default=list)  # [{step, type, params, wait}]
    is_active: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class JourneyInstance(Base, UUIDPk, Timestamped):
    """Durable journey state — survives restarts (§44: no process memory)."""

    __tablename__ = "journey_instances"
    __table_args__ = (Index("ix_journey_wait", "status", "wait_until"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    journey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journeys.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|waiting|completed|cancelled|failed
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    wait_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JourneyStepLog(Base, UUIDPk):
    __tablename__ = "journey_step_logs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journey_instances.id"), index=True)
    step_no: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="done")  # done|skipped|failed
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SLAPolicy(Base, UUIDPk, Timestamped):
    """Deterministic SLA engine (§29)."""

    __tablename__ = "sla_policies"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    entity_type: Mapped[str] = mapped_column(String(40))  # lead|conversation|viewing
    trigger_event: Mapped[str] = mapped_column(String(150))
    target_seconds: Mapped[int] = mapped_column(Integer)
    warn_seconds: Mapped[int | None] = mapped_column(Integer)
    escalations: Mapped[list] = mapped_column(JSONB, default=list)  # [{at_seconds, action, params}]
    is_active: Mapped[bool] = mapped_column(default=True)


class SLATracker(Base, UUIDPk):
    __tablename__ = "sla_trackers"
    __table_args__ = (Index("ix_sla_due", "status", "due_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sla_policies.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="on_track")  # on_track|warned|breached|resolved|met
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base, UUIDPk):
    __tablename__ = "notifications"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="in_app")  # in_app|email|sms|whatsapp
    recipient_type: Mapped[str] = mapped_column(String(20), default="user")  # user|person
    recipient_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    template: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|sent|failed|read
    related_entity_type: Mapped[str | None] = mapped_column(String(60))
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
