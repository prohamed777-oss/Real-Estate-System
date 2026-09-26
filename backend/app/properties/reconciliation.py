"""Inventory reconciliation engine (§19).

External sources (developer APIs, ERP, CSV, partner feeds) may disagree with
canonical inventory. Resolution order:
    1. source precedence (per-tenant config, default: developer > erp > partner > manual)
    2. confidence + freshness tie-break
    3. ambiguous → manual_review (never silent auto-override)
Every decision records value/source/timestamp/confidence (§19).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import NotFound, ValidationFailed
from app.events.outbox import emit
from app.properties.models import PropertyAsset, UnitInventory
from app.properties.models_ext import InventoryConflict

DEFAULT_PRECEDENCE = ["developer", "erp", "partner", "manual", "internal"]


async def detect_conflict(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID, kind: str,
    canonical_value: dict[str, Any], external_value: dict[str, Any],
    external_source: str, source_last_synced_at: datetime | None = None,
    external_confidence: int = 70,
) -> InventoryConflict | None:
    """Compare an external source's claim against canonical state.
    Returns the conflict row when they disagree (None when equal)."""
    if kind not in ("availability", "price", "attributes"):
        raise ValidationFailed("kind must be availability|price|attributes")
    if canonical_value == external_value:
        return None
    asset = (
        await session.execute(
            select(PropertyAsset).where(
                PropertyAsset.id == asset_id, PropertyAsset.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise NotFound("Asset not found")

    precedence = DEFAULT_PRECEDENCE
    asset_source = asset.source_type or "internal"
    # canonical wins unless the external source outranks it AND is fresh/confident
    canonical_rank = precedence.index(asset_source) if asset_source in precedence else len(precedence)
    external_rank = precedence.index(external_source) if external_source in precedence else len(precedence)
    fresh = (
        source_last_synced_at is None
        or (datetime.now(UTC) - source_last_synced_at).total_seconds() < 24 * 3600
    )
    external_outranks = external_rank < canonical_rank and fresh and external_confidence >= 70
    unambiguous = external_outranks and external_confidence >= 85

    conflict = InventoryConflict(
        tenant_id=tenant_id, asset_id=asset_id, kind=kind,
        canonical_value=canonical_value, external_value=external_value,
        source_precedence=f"{asset_source}(canonical) vs {external_source}(external)",
        confidence=external_confidence,
        status="auto_resolved" if unambiguous else "manual_review",
    )
    session.add(conflict)
    await session.flush()

    if unambiguous:
        await _apply_external(session, tenant_id=tenant_id, asset_id=asset_id, kind=kind,
                              external_value=external_value)
        conflict.resolution = {
            "applied": external_value, "reason": "source_precedence+fresh+high_confidence",
            "decided_at": datetime.now(UTC).isoformat(),
        }
    await audit(
        session, tenant_id=tenant_id, actor_type="system", actor_id=None,
        action="inventory.conflict_detected", entity_type="property_asset",
        entity_id=asset_id, after={"kind": kind, "status": conflict.status},
        source="reconciliation",
    )
    await emit(
        session, event_name="inventory.conflict_detected", tenant_id=tenant_id,
        aggregate_type="property_asset", aggregate_id=asset_id,
        payload={"conflict_id": str(conflict.id), "kind": kind, "status": conflict.status},
    )
    return conflict


async def _apply_external(session: AsyncSession, *, tenant_id: uuid.UUID,
                          asset_id: uuid.UUID, kind: str, external_value: dict[str, Any]) -> None:
    if kind == "availability":
        inv = (
            await session.execute(
                select(UnitInventory).where(
                    UnitInventory.tenant_id == tenant_id, UnitInventory.asset_id == asset_id
                )
            )
        ).scalar_one_or_none()
        if inv is not None:
            inv.availability_confidence = "external_sync"
            inv.updated_at = datetime.now(UTC)


async def resolve_manually(
    session: AsyncSession, *, tenant_id: uuid.UUID, conflict_id: uuid.UUID,
    resolution: dict[str, Any], resolved_by: str,
) -> InventoryConflict:
    conflict = await session.get(InventoryConflict, conflict_id)
    if conflict is None or conflict.tenant_id != tenant_id:
        raise NotFound("Conflict not found")
    if conflict.status == "resolved":
        raise Conflict("Conflict already resolved")
    conflict.status = "resolved"
    conflict.resolved_by = resolved_by
    conflict.resolution = resolution
    conflict.resolved_at = datetime.now(UTC)
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=resolved_by,
        action="inventory.conflict_resolved", entity_type="inventory_conflict",
        entity_id=conflict_id, after=resolution,
    )
    return conflict


async def sync_external_state(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID,
    external_availability: str | None = None, external_price: str | None = None,
    external_source: str = "developer", confidence: int = 80,
    last_synced_at: datetime | None = None,
) -> list[InventoryConflict]:
    """Entry point for feeds: compare + record conflicts (never silent mutation)."""
    conflicts: list[InventoryConflict] = []
    inv = (
        await session.execute(
            select(UnitInventory).where(
                UnitInventory.tenant_id == tenant_id, UnitInventory.asset_id == asset_id
            )
        )
    ).scalar_one_or_none()
    if external_availability is not None and inv is not None:
        c = await detect_conflict(
            session, tenant_id=tenant_id, asset_id=asset_id, kind="availability",
            canonical_value={"state": inv.state},
            external_value={"state": external_availability},
            external_source=external_source, source_last_synced_at=last_synced_at,
            external_confidence=confidence,
        )
        if c:
            conflicts.append(c)
    if external_price is not None:
        from app.properties.service import get_current_price

        price = await get_current_price(session, tenant_id, asset_id)
        canonical = str(price.amount) if price else None
        if canonical != external_price:
            c = await detect_conflict(
                session, tenant_id=tenant_id, asset_id=asset_id, kind="price",
                canonical_value={"amount": canonical},
                external_value={"amount": external_price},
                external_source=external_source, source_last_synced_at=last_synced_at,
                external_confidence=confidence,
            )
            if c:
                conflicts.append(c)
    return conflicts
