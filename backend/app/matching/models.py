"""Search & Matching models (§24-25, §105).

property_search_documents is a READ model — Search Index ≠ source of truth.
Reservations/pricing/availability always go back to the canonical domains.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.core.config import settings
from app.core.db import Base, UUIDPk


class PropertySearchDocument(Base, UUIDPk):
    __tablename__ = "property_search_documents"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("property_assets.id"), index=True)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    doc: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # flattened searchable fields
    embedding: Mapped[list | None] = mapped_column(
        Vector(settings.embedding_dimensions), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class LeadEmbedding(Base, UUIDPk):
    __tablename__ = "lead_embeddings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    embedding: Mapped[list | None] = mapped_column(Vector(settings.embedding_dimensions), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class MatchingRun(Base, UUIDPk):
    """One execution of the matching pipeline with full score breakdown (§25)."""

    __tablename__ = "matching_runs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"), index=True)
    engine_version: Mapped[str] = mapped_column(String(30), default="v1")
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    results: Mapped[list] = mapped_column(JSONB, default=list)  # [{asset_id, scores:{...}, reasons}]
    candidates_considered: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
