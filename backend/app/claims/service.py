"""Claims — the versioned, proven truth layer (V4 4.4-4.5).

Every critical fact can be recorded as a CLAIM with provenance
(source, assertion_type, confidence) and bitemporal validity
(valid time + system time). EXTRACTED/INFERRED claims are born
UNVERIFIED — they reach CURRENT only through explicit verification
(doc rule: external data is never silently trusted).

Bitemporal queries answer:
  - What was TRUE at time T?         (valid-time filter)
  - What did the system BELIEVE at T? (recorded-time filter)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy import select

from app.core.db import Base, UUIDPk
from app.core.errors import NotFound, ValidationFailed

TRUTH_STATUSES = {"CURRENT", "STALE", "CONFLICTED", "UNVERIFIED", "SUPERSEDED"}
ASSERTION_TYPES = {"OBSERVED", "EXTRACTED", "INFERRED", "PREDICTED", "SYSTEM_DERIVED"}
# assertion types that require explicit verification before CURRENT
UNTRUSTED_ASSERTIONS = {"EXTRACTED", "INFERRED"}


class Claim(Base, UUIDPk):
    __tablename__ = "claims"
    __table_args__ = (
        Index("ix_claims_entity_field", "tenant_id", "entity_type", "entity_id", "field"),
        Index("ix_claims_status", "truth_status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    field: Mapped[str] = mapped_column(String(120))
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {"v": ...}
    source: Mapped[str | None] = mapped_column(String(120))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[int] = mapped_column(Integer, default=70)
    truth_status: Mapped[str] = mapped_column(String(20), default="UNVERIFIED")
    assertion_type: Mapped[str] = mapped_column(String(20), default="OBSERVED")
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(String(120))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


async def assert_claim(
    session, *, tenant_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID,
    field: str, value: Any, source: str | None = None,
    source_timestamp: datetime | None = None, confidence: int = 70,
    assertion_type: str = "OBSERVED", actor_id: str | None = None,
) -> Claim:  # noqa: ANN001
    """Record a new claim version. Supersedes the previous CURRENT claim.

    EXTRACTED/INFERRED claims are born UNVERIFIED — they must pass an explicit
    verification step before being treated as trusted truth."""
    if assertion_type not in ASSERTION_TYPES:
        raise ValidationFailed(f"assertion_type must be one of {sorted(ASSERTION_TYPES)}")
    if confidence < 0 or confidence > 100:
        raise ValidationFailed("confidence must be 0-100")

    now = datetime.now(UTC)
    previous = (
        await session.execute(
            select(Claim).where(
                Claim.tenant_id == tenant_id,
                Claim.entity_type == entity_type,
                Claim.entity_id == entity_id,
                Claim.field == field,
                Claim.truth_status.in_(("CURRENT", "UNVERIFIED", "STALE", "CONFLICTED")),
            )
        )
    ).scalars().all()
    for prev in previous:
        prev.truth_status = "SUPERSEDED"
        prev.superseded_at = now

    initial_status = "UNVERIFIED" if assertion_type in UNTRUSTED_ASSERTIONS else "CURRENT"
    claim = Claim(
        tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id,
        field=field, value={"v": value}, source=source,
        source_timestamp=source_timestamp or now, confidence=confidence,
        truth_status=initial_status, assertion_type=assertion_type,
        verified_by=actor_id if initial_status == "CURRENT" else None,
        verified_at=now if initial_status == "CURRENT" else None,
    )
    session.add(claim)
    await session.flush()
    return claim


async def verify_claim(session, *, tenant_id: uuid.UUID, claim_id: uuid.UUID,
                       verified_by: str | None = None) -> Claim:  # noqa: ANN001
    """Explicit verification gate: UNVERIFIED → CURRENT (V4 4.4 rule)."""
    claim = await session.get(Claim, claim_id)
    if claim is None or claim.tenant_id != tenant_id:
        raise NotFound("Claim not found")
    if claim.truth_status == "CURRENT":
        return claim
    if claim.truth_status == "SUPERSEDED":
        raise ValidationFailed("Cannot verify a superseded claim")
    claim.truth_status = "CURRENT"
    claim.verified_at = datetime.now(UTC)
    claim.verified_by = verified_by
    await session.flush()
    return claim


async def mark_stale(session, *, tenant_id: uuid.UUID, entity_type: str,
                     entity_id: uuid.UUID, field: str) -> int:  # noqa: ANN001
    """Freshness contract: when canonical state moves, old claims go STALE."""
    from sqlalchemy import update

    res = await session.execute(
        update(Claim)
        .where(
            Claim.tenant_id == tenant_id,
            Claim.entity_type == entity_type,
            Claim.entity_id == entity_id,
            Claim.field == field,
            Claim.truth_status == "CURRENT",
        )
        .values(truth_status="STALE")
    )
    return res.rowcount or 0


async def get_truth(session, *, tenant_id: uuid.UUID, entity_type: str,
                    entity_id: uuid.UUID, field: str | None = None,
                    as_of_valid: datetime | None = None,
                    as_of_recorded: datetime | None = None,
                    include_all: bool = False) -> list[Claim]:  # noqa: ANN001
    """Bitemporal truth query (V4 4.5)."""
    from sqlalchemy import select

    query = select(Claim).where(
        Claim.tenant_id == tenant_id,
        Claim.entity_type == entity_type,
        Claim.entity_id == entity_id,
    )
    if field:
        query = query.where(Claim.field == field)
    if not include_all:
        query = query.where(Claim.truth_status.in_(("CURRENT", "UNVERIFIED")))
    if as_of_valid is not None:
        query = query.where(
            Claim.valid_from <= as_of_valid,
            (Claim.valid_to.is_(None)) | (Claim.valid_to > as_of_valid),
        )
    if as_of_recorded is not None:
        query = query.where(Claim.recorded_at <= as_of_recorded)
    rows = (
        await session.execute(query.order_by(Claim.recorded_at.desc()))
    ).scalars().all()
    return rows
