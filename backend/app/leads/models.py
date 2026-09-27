"""Lead domain models (§8-11): acquisition entry + structured requirements + scores."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class Lead(Base, UUIDPk, Timestamped):
    __tablename__ = "leads"
    __table_args__ = (Index("ix_leads_lifecycle", "tenant_id", "lifecycle_stage"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id"), index=True)
    source: Mapped[str | None] = mapped_column(String(100))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"))

    lifecycle_stage: Mapped[str] = mapped_column(String(30), default="NEW")
    qualification_state: Mapped[str] = mapped_column(String(30), default="PENDING")

    # Scoring engine outputs (one engine, multiple signals — review rule #2)
    lead_score: Mapped[int] = mapped_column(Integer, default=0)
    intent_score: Mapped[int] = mapped_column(Integer, default=0)
    engagement_score: Mapped[int] = mapped_column(Integer, default=0)
    fit_score: Mapped[int] = mapped_column(Integer, default=0)
    score_version: Mapped[str] = mapped_column(String(30), default="v1")
    score_signals: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    score_calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    next_action: Mapped[str | None] = mapped_column(String(40))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action_reason: Mapped[str | None] = mapped_column(Text)
    dormant_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lost_reason: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[str | None] = mapped_column(String(40))  # user|ai|system|webhook


class LeadRequirement(Base, UUIDPk, Timestamped):
    """Structured requirements (§10) with explicit/behavioral separation."""

    __tablename__ = "lead_requirements"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), unique=True, index=True)
    explicit: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    behavioral: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    objections: Mapped[list] = mapped_column(JSONB, default=list)
    timeline: Mapped[str | None] = mapped_column(String(60))
    confidence: Mapped[int] = mapped_column(Integer, default=0)  # 0-100 extraction confidence
    source: Mapped[str] = mapped_column(String(30), default="manual")  # manual|ai|import
