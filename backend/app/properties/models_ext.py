"""Full document lifecycle (§36) + inventory reconciliation (§19).

documents/document_versions/document_approvals/document_signatures:
file storage is NOT document management — versioning, approvals, signatures.

inventory_conflicts: multi-source conflicts (§19) resolved by source precedence,
with manual review for ambiguity.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPk


class DocumentVersion(Base, UUIDPk):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_doc_version"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128))
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocumentApproval(Base, UUIDPk):
    __tablename__ = "document_approvals"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    approver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    notes: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocumentSignature(Base, UUIDPk):
    __tablename__ = "document_signatures"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    signer_name: Mapped[str] = mapped_column(String(200))
    signer_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("people.id"))
    signer_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str | None] = mapped_column(String(60))  # buyer|seller|broker|witness
    method: Mapped[str] = mapped_column(String(40), default="manual")  # manual|digital|otp
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|signed|declined
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class InventoryConflict(Base, UUIDPk):
    """§19: external sources may disagree — resolve by precedence, review ambiguous."""

    __tablename__ = "inventory_conflicts"
    __table_args__ = (Index("ix_inv_conflicts_open", "tenant_id", "status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("property_assets.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # availability|price|attributes
    canonical_value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    external_value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_precedence: Mapped[str | None] = mapped_column(String(60))
    confidence: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|auto_resolved|manual_review|resolved
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    resolution: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
