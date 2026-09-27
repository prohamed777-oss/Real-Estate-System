"""Matching API: search + match-for-lead."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.permissions import AI_RUN, PROPERTIES_READ, require
from app.core.tenancy import AuthContext
from app.events.queue import enqueue
from app.matching.models import MatchingRun, PropertySearchDocument
from app.matching.service import embed_lead, run_matching
from app.properties.service import check_availability

router = APIRouter(tags=["matching"])


class SearchFilters(BaseModel):
    city: str | None = None
    area: str | None = None
    property_type: str | None = None
    min_bedrooms: int | None = None
    max_price: float | None = None
    min_price: float | None = None
    query: str | None = None


@router.get("/search/properties")
async def search_properties(
    auth: AuthContext = Depends(require(PROPERTIES_READ)),
    session: AsyncSession = Depends(get_session),
    city: str | None = None,
    area: str | None = None,
    property_type: str | None = None,
    min_bedrooms: int | None = None,
    max_price: float | None = None,
    limit: int = Query(default=20, le=100),
) -> list[dict[str, Any]]:
    """Structured search over the read model (§24). Then availability from canonical inventory."""
    query = select(PropertySearchDocument).where(
        PropertySearchDocument.tenant_id == auth.tenant_id
    )
    rows = (await session.execute(query.limit(500))).scalars().all()
    results = []
    for r in rows:
        d = r.doc
        if city and (d.get("city") or "").lower().find(city.lower()) < 0:
            continue
        if area and (d.get("area") or "").lower().find(area.lower()) < 0:
            continue
        if property_type and (d.get("property_type") or "").lower() != property_type.lower():
            continue
        if min_bedrooms is not None and (d.get("bedrooms") or 0) < min_bedrooms:
            continue
        if max_price is not None and d.get("price_amount") and d["price_amount"] > max_price:
            continue
        results.append({k: v for k, v in d.items() if k != "attributes"})
    asset_ids = [uuid.UUID(r["asset_id"]) for r in results[:limit]]
    availability = (
        await check_availability(session, tenant_id=auth.tenant_id, asset_ids=asset_ids)
        if asset_ids else {}
    )
    for r in results:
        r["availability"] = availability.get(r["asset_id"])
    return results[:limit]


class MatchIn(BaseModel):
    lead_id: uuid.UUID
    top_n: int = 10


@router.post("/sync")
async def sync_search_documents(
    auth: AuthContext = Depends(require(AI_RUN)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Reconciliation tool: rebuild search docs for every active asset (§19)."""
    from sqlalchemy import select as sel

    from app.matching.service import build_search_document
    from app.properties.models import PropertyAsset

    assets = (
        await session.execute(
            sel(PropertyAsset).where(
                PropertyAsset.tenant_id == auth.tenant_id, PropertyAsset.is_active.is_(True)
            )
        )
    ).scalars().all()
    for asset in assets:
        await build_search_document(session, tenant_id=auth.tenant_id, asset_id=asset.id)
    await enqueue(
        session, job_type="matching.embed_documents", tenant_id=auth.tenant_id, payload={},
    )
    return {"synced": len(assets)}


@router.post("/match")
async def match_for_lead(
    body: MatchIn,
    auth: AuthContext = Depends(require(AI_RUN)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await embed_lead(session, tenant_id=auth.tenant_id, lead_id=body.lead_id)
    run = await run_matching(
        session, tenant_id=auth.tenant_id, lead_id=body.lead_id, top_n=body.top_n
    )
    return {
        "run_id": str(run.id), "engine_version": run.engine_version,
        "candidates_considered": run.candidates_considered,
        "results": run.results,
    }


@router.get("/match/runs")
async def list_runs(
    auth: AuthContext = Depends(require(AI_RUN)),
    session: AsyncSession = Depends(get_session),
    lead_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    query = select(MatchingRun).where(MatchingRun.tenant_id == auth.tenant_id)
    if lead_id:
        query = query.where(MatchingRun.lead_id == lead_id)
    rows = (
        await session.execute(query.order_by(MatchingRun.created_at.desc()).limit(20))
    ).scalars().all()
    return [
        {"id": str(r.id), "lead_id": str(r.lead_id), "engine_version": r.engine_version,
         "top_n": len(r.results), "created_at": r.created_at.isoformat()}
        for r in rows
    ]
