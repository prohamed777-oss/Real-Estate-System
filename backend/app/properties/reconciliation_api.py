"""Reconciliation API (§19) + inventory conflicts review."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFound
from app.core.permissions import INVENTORY_READ, INVENTORY_WRITE, PROPERTIES_WRITE, require
from app.core.tenancy import AuthContext
from app.properties.models_ext import InventoryConflict
from app.properties.reconciliation import resolve_manually, sync_external_state

router = APIRouter(prefix="/inventory", tags=["reconciliation"])


class SyncIn(BaseModel):
    asset_id: uuid.UUID
    external_availability: str | None = None
    external_price: str | None = None
    external_source: str = "developer"
    confidence: int = 80
    last_synced_at: str | None = None


class ResolveIn(BaseModel):
    resolution: dict[str, Any]


@router.post("/sync-external")
async def post_sync_external(
    body: SyncIn,
    auth: AuthContext = Depends(require(PROPERTIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Feed entry point: compare external claims against canonical inventory (§19)."""
    conflicts = await sync_external_state(
        session, tenant_id=auth.tenant_id, asset_id=body.asset_id,
        external_availability=body.external_availability,
        external_price=body.external_price,
        external_source=body.external_source, confidence=body.confidence,
        last_synced_at=datetime.fromisoformat(body.last_synced_at) if body.last_synced_at else None,
    )
    return {
        "conflicts": [
            {"id": str(c.id), "kind": c.kind, "status": c.status,
             "canonical": c.canonical_value, "external": c.external_value}
            for c in conflicts
        ]
    }


@router.get("/conflicts")
async def list_conflicts(
    auth: AuthContext = Depends(require(INVENTORY_READ)),
    session: AsyncSession = Depends(get_session),
    status: str = "manual_review",
) -> list[dict[str, Any]]:
    query = select(InventoryConflict).where(InventoryConflict.tenant_id == auth.tenant_id)
    if status != "all":
        query = query.where(InventoryConflict.status == status)
    rows = (
        await session.execute(query.order_by(InventoryConflict.detected_at.desc()).limit(100))
    ).scalars().all()
    return [
        {"id": str(c.id), "asset_id": str(c.asset_id), "kind": c.kind, "status": c.status,
         "canonical": c.canonical_value, "external": c.external_value,
         "precedence": c.source_precedence, "confidence": c.confidence,
         "detected_at": c.detected_at.isoformat()}
        for c in rows
    ]


@router.post("/conflicts/{conflict_id}/resolve")
async def post_resolve(
    conflict_id: uuid.UUID,
    body: ResolveIn,
    auth: AuthContext = Depends(require(INVENTORY_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    conflict = await resolve_manually(
        session, tenant_id=auth.tenant_id, conflict_id=conflict_id,
        resolution=body.resolution, resolved_by=str(auth.user_id),
    )
    return {"id": str(conflict.id), "status": conflict.status}
