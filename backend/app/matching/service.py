"""Search document sync + matching execution (§25, §105)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import get_embedding_provider
from app.core.errors import NotFound
from app.events.queue import enqueue
from app.events.outbox import emit
from app.leads.models import Lead, LeadRequirement
from app.matching.engine import ENGINE_VERSION, rank_candidates
from app.matching.models import LeadEmbedding, MatchingRun, PropertySearchDocument
from app.properties.models import PriceVersion, PropertyAsset, UnitInventory
from app.properties.service import check_availability


async def build_search_document(session: AsyncSession, *, tenant_id: uuid.UUID,
                                asset_id: uuid.UUID) -> PropertySearchDocument:
    asset = (
        await session.execute(
            select(PropertyAsset).where(
                PropertyAsset.id == asset_id, PropertyAsset.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise NotFound("Asset not found")
    price = (
        await session.execute(
            select(PriceVersion).where(
                PriceVersion.tenant_id == tenant_id,
                PriceVersion.asset_id == asset_id,
                PriceVersion.valid_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    inv = (
        await session.execute(
            select(UnitInventory).where(
                UnitInventory.tenant_id == tenant_id, UnitInventory.asset_id == asset_id
            )
        )
    ).scalar_one_or_none()

    doc = {
        "asset_id": str(asset.id),
        "title": asset.title,
        "property_type": asset.property_type,
        "purpose": asset.purpose,
        "asset_type": asset.asset_type,
        "project_id": str(asset.project_id) if asset.project_id else None,
        "bedrooms": asset.bedrooms,
        "bathrooms": asset.bathrooms,
        "area_value": float(asset.area_value) if asset.area_value else None,
        "finishing": asset.finishing,
        "floor": asset.floor,
        "view": asset.view,
        "delivery_status": asset.delivery_status,
        "city": (asset.location or {}).get("city"),
        "area": (asset.location or {}).get("area"),
        "compound": (asset.location or {}).get("compound"),
        "price_amount": float(price.amount) if price else None,
        "price_currency": price.currency if price else None,
        "attributes": asset.attributes or {},
        "inventory_state": inv.state if inv else ("AVAILABLE" if asset.asset_type != "unit" else None),
        "updated_at": asset.updated_at.isoformat(),
    }
    existing = (
        await session.execute(
            select(PropertySearchDocument).where(
                PropertySearchDocument.tenant_id == tenant_id,
                PropertySearchDocument.asset_id == asset_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.doc = doc
        existing.embedded_at = None  # stale embedding → re-embed
        return existing
    row = PropertySearchDocument(tenant_id=tenant_id, asset_id=asset_id, doc=doc)
    session.add(row)
    await session.flush()
    return row


async def embed_search_documents(session: AsyncSession, *, tenant_id: uuid.UUID,
                                 limit: int = 100) -> int:
    """Job: embed stale search docs. Failure-safe; retried by queue."""
    rows = (
        await session.execute(
            select(PropertySearchDocument)
            .where(
                PropertySearchDocument.tenant_id == tenant_id,
                PropertySearchDocument.embedded_at.is_(None),
            )
            .limit(limit)
        )
    ).scalars().all()
    if not rows:
        return 0
    provider = get_embedding_provider()
    texts = [_doc_text(r.doc) for r in rows]
    vectors = await provider.embed(texts)
    for row, vec in zip(rows, vectors):
        row.embedding = vec
        row.embedding_model = provider.name
        row.embedded_at = datetime.now(UTC)
    await session.flush()
    return len(rows)


def _doc_text(doc: dict[str, Any]) -> str:
    parts = [
        doc.get("title") or "", doc.get("property_type") or "",
        f"{doc.get('bedrooms') or ''} bedrooms",
        doc.get("finishing") or "", doc.get("area") or "", doc.get("city") or "",
        doc.get("view") or "",
    ]
    return " ".join(str(p) for p in parts if p)


async def embed_lead(session: AsyncSession, *, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> None:
    req = (
        await session.execute(
            select(LeadRequirement).where(LeadRequirement.lead_id == lead_id)
        )
    ).scalar_one_or_none()
    explicit = (req.explicit if req else {}) or {}
    text = " ".join(
        str(x) for x in [
            explicit.get("property_types") or [], explicit.get("areas") or [],
            f"budget {explicit.get('max_budget') or ''}",
            f"{explicit.get('bedrooms') or ''} bedrooms",
            explicit.get("finishing_preference") or "",
            explicit.get("delivery_preference") or "",
        ] if x
    )
    if not text.strip():
        return
    provider = get_embedding_provider()
    vec = (await provider.embed([text]))[0]
    row = (
        await session.execute(
            select(LeadEmbedding).where(LeadEmbedding.lead_id == lead_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = LeadEmbedding(tenant_id=tenant_id, lead_id=lead_id)
        session.add(row)
    row.embedding = vec
    row.embedding_model = provider.name
    await session.flush()


async def run_matching(session: AsyncSession, *, tenant_id: uuid.UUID, lead_id: uuid.UUID,
                       top_n: int = 10) -> MatchingRun:
    """Full pipeline (§25). Results are stored with score breakdowns."""
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if lead is None:
        raise NotFound("Lead not found")
    req_row = (
        await session.execute(select(LeadRequirement).where(LeadRequirement.lead_id == lead_id))
    ).scalar_one_or_none()
    requirements: dict[str, Any] = (req_row.explicit if req_row else {}) or {}
    behavioral: dict[str, Any] = (req_row.behavioral if req_row else {}) or {}

    lead_emb_row = (
        await session.execute(select(LeadEmbedding).where(LeadEmbedding.lead_id == lead_id))
    ).scalar_one_or_none()

    # SQL-side vector KNN pushdown (pgvector <=>): shortlist via index instead of
    # loading every document — Python scores only the shortlist.
    if lead_emb_row is not None and lead_emb_row.embedding is not None:
        docs = (
            await session.execute(
                select(PropertySearchDocument)
                .where(PropertySearchDocument.tenant_id == tenant_id)
                .order_by(PropertySearchDocument.embedding.cosine_distance(lead_emb_row.embedding))
                .limit(60)
            )
        ).scalars().all()
    else:
        docs = (
            await session.execute(
                select(PropertySearchDocument)
                .where(PropertySearchDocument.tenant_id == tenant_id)
                .limit(300)
            )
        ).scalars().all()
    doc_list = [d.doc | {"asset_id": str(d.asset_id), "embedding": d.embedding} for d in docs]

    asset_ids = [uuid.UUID(d["asset_id"]) for d in doc_list]
    availability_map = (
        await check_availability(session, tenant_id=tenant_id, asset_ids=asset_ids)
        if asset_ids else {}
    )

    results = rank_candidates(
        docs=doc_list,
        requirements=requirements,
        availability_map=availability_map,
        lead_embedding=lead_emb_row.embedding if lead_emb_row else None,
        behavioral=behavioral,
        top_n=top_n,
    )
    run = MatchingRun(
        tenant_id=tenant_id, lead_id=lead_id, engine_version=ENGINE_VERSION,
        params={"requirements": requirements, "top_n": top_n},
        results=results, candidates_considered=len(doc_list),
    )
    session.add(run)
    await session.flush()
    await emit(
        session, event_name="matching.completed", tenant_id=tenant_id,
        aggregate_type="lead", aggregate_id=lead_id,
        payload={"lead_id": str(lead_id), "run_id": str(run.id), "top": len(results),
                 "top_asset_id": results[0]["asset_id"] if results else None},
    )
    return run
