"""Finance models (§36-39): contracts, documents, payments, commissions, deals."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class Document(Base, UUIDPk, Timestamped):
    """Document management ≠ file storage (§36)."""

    __tablename__ = "documents"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60), index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    # offer|reservation_form|contract|broker_agreement|commission_agreement|identity|property_doc
    kind: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300))
    storage_path: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft")  # draft|final|approved|archived
    source: Mapped[str] = mapped_column(String(30), default="upload")  # upload|generated|ocr
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    checksum: Mapped[str | None] = mapped_column(String(128))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    # OCR/AI facts — NOT truth until approved (§85)
    extracted_facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    facts_verified: Mapped[bool] = mapped_column(default=False)


class Contract(Base, UUIDPk, Timestamped):
    __tablename__ = "contracts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    buyer_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id"))
    type: Mapped[str] = mapped_column(String(60), default="contract")
    # draft|pending_signature|active|completed|terminated
    status: Mapped[str] = mapped_column(String(30), default="draft")
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    financial_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(64))


class Deal(Base, UUIDPk, Timestamped):
    """The commercial outcome (§39)."""

    __tablename__ = "deals"
    __table_args__ = (Index("ix_deals_status", "tenant_id", "status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("opportunities.id"), index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contracts.id"))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id"), index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30), default="OPEN")  # OPEN|CONTRACTED|WON|LOST|CANCELLED
    gross_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    net_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    financial_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    participants: Mapped[list] = mapped_column(JSONB, default=list)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lost_reason: Mapped[str | None] = mapped_column(Text)


class Payment(Base, UUIDPk, Timestamped):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_deal", "tenant_id", "deal_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id"))
    direction: Mapped[str] = mapped_column(String(10), default="inbound")  # inbound|outbound
    # deposit|installment|fee|refund|commission_payout
    kind: Mapped[str] = mapped_column(String(40), default="installment")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    # scheduled|pending|completed|failed|refunded|cancelled
    status: Mapped[str] = mapped_column(String(30), default="scheduled")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    method: Mapped[str | None] = mapped_column(String(60))
    provider: Mapped[str | None] = mapped_column(String(60))
    provider_ref: Mapped[str | None] = mapped_column(String(200))
    reference: Mapped[str | None] = mapped_column(String(200))


class PaymentSchedule(Base, UUIDPk):
    __tablename__ = "payment_schedules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    installment_no: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    payment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CommissionRule(Base, UUIDPk, Timestamped):
    """Configurable commission rules (§38)."""

    __tablename__ = "commission_rules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {project_id?, branch_id?, team_id?}
    basis: Mapped[str] = mapped_column(String(30), default="deal_value")  # deal_value|collected
    # {agency, sales_rep, broker, referrer, developer} pcts
    splits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(default=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Commission(Base, UUIDPk, Timestamped):
    __tablename__ = "commissions"
    __table_args__ = (Index("ix_commissions_deal", "tenant_id", "deal_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    deal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commission_rules.id"))
    beneficiary_type: Mapped[str] = mapped_column(String(30))  # agency|user|partner|developer
    beneficiary_id: Mapped[str | None] = mapped_column(String(64))
    basis_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    status: Mapped[str] = mapped_column(String(30), default="calculated")  # calculated|approved|paid|cancelled
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payout_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
