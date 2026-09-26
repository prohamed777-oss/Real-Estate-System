"""Decision Plane persistence (V4 PART 5) + Inventory Ledger (3.4) + Commission Splits (1.29)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    text as sa_text,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk


class ApprovalRequest(Base, UUIDPk):
    """THE single approval primitive (V4 5.4, Principle 1.27).

    subject_type examples: offer_discount, contract, reservation_exception,
    high_value_outbound, marketplace_app_install, custom_webhook_registration,
    content_publish.
    Every domain CONSUMES approvals — nobody writes private approval logic.
    """

    __tablename__ = "approval_requests"
    __table_args__ = (Index("ix_approvals_status", "tenant_id", "status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(60))
    subject_id: Mapped[str] = mapped_column(String(120))
    requested_by: Mapped[str | None] = mapped_column(String(120))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    payload_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    policy_reference: Mapped[str | None] = mapped_column(String(200))
    approver_role_required: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING|APPROVED|REJECTED|EXPIRED|ESCALATED
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Decision(Base, UUIDPk):
    """Decision Record (V4 5.3) — every major decision reproducible:
    which policy said what, which scores, which optimization, which approval."""

    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_subject", "tenant_id", "action"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    action: Mapped[str] = mapped_column(String(120))  # ASSIGN_LEAD | APPROVE_DISCOUNT | ...
    reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    policy_evaluation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    scoring_results: Mapped[list] = mapped_column(JSONB, default=list)
    optimization_result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ai_execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    selected_option: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    decision_version: Mapped[str] = mapped_column(String(30), default="v1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CommissionSplit(Base):
    """Financial invariant (V4 1.29): splits enforced BY THE DATABASE.
    A trigger guarantees SUM(share_percentage) per deal — violations ROLLBACK."""

    __tablename__ = "commission_splits"
    __table_args__ = (
        UniqueConstraint("deal_id", "party_role", "party_id", name="uq_split_party"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
    )
    deal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    party_role: Mapped[str] = mapped_column(String(40))  # agency|sales_rep|broker|referrer|developer
    party_id: Mapped[str | None] = mapped_column(String(120))
    share_percentage: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), CheckConstraint(
            "share_percentage >= 0 AND share_percentage <= 100",
            name="ck_split_share_range"
        )
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class InventoryLedger(Base):
    """Append-only inventory history (V4 3.4): every state change, one row,
    never deleted. Audit at the domain's own granularity."""

    __tablename__ = "inventory_ledger"
    __table_args__ = (Index("ix_inv_ledger_unit", "tenant_id", "asset_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    from_state: Mapped[str | None] = mapped_column(String(30))
    to_state: Mapped[str] = mapped_column(String(30))
    actor_type: Mapped[str] = mapped_column(String(20), default="user")
    actor_id: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(String(300))
    tx_id: Mapped[str | None] = mapped_column(String(64))  # request/trace correlation
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
