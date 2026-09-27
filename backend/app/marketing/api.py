"""Marketing API (§40-42): campaigns, lead sources, content engine."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFound
from app.core.permissions import MARKETING_READ, MARKETING_WRITE, require
from app.core.tenancy import AuthContext
from app.marketing.models import Attribution, Campaign
from app.marketing.service import (
    create_campaign,
    generate_content,
    publish_content,
    review_content,
    upsert_lead_source,
)

router = APIRouter(prefix="/marketing", tags=["marketing"])


class CampaignIn(BaseModel):
    name: str
    channel: str | None = None
    budget: Decimal | None = None
    currency: str = "EGP"
    utm: dict[str, Any] = {}


class LeadSourceIn(BaseModel):
    key: str
    name: str
    channel: str | None = None
    campaign_id: uuid.UUID | None = None


@router.post("/campaigns", status_code=201)
async def post_campaign(
    body: CampaignIn,
    auth: AuthContext = Depends(require(MARKETING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    campaign = await create_campaign(
        session, tenant_id=auth.tenant_id, name=body.name, channel=body.channel,
        budget=float(body.budget) if body.budget else None, currency=body.currency,
        utm=body.utm, actor_id=auth.user_id,
    )
    return {"id": str(campaign.id), "name": campaign.name, "status": campaign.status}


@router.get("/campaigns")
async def list_campaigns(
    auth: AuthContext = Depends(require(MARKETING_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Campaign).where(Campaign.tenant_id == auth.tenant_id)
        )
    ).scalars().all()
    return [
        {"id": str(c.id), "name": c.name, "channel": c.channel, "status": c.status,
         "budget": str(c.budget) if c.budget else None, "spend": str(c.spend) if c.spend else None}
        for c in rows
    ]


@router.post("/lead-sources", status_code=201)
async def post_lead_source(
    body: LeadSourceIn,
    auth: AuthContext = Depends(require(MARKETING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await upsert_lead_source(
        session, tenant_id=auth.tenant_id, key=body.key, name=body.name,
        channel=body.channel, campaign_id=body.campaign_id,
    )
    return {"id": str(row.id), "key": row.key}


@router.get("/attribution/{lead_id}")
async def get_attribution(
    lead_id: uuid.UUID,
    auth: AuthContext = Depends(require(MARKETING_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(Attribution).where(
                Attribution.tenant_id == auth.tenant_id, Attribution.lead_id == lead_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("No attribution for this lead")
    return {
        "lead_id": str(row.lead_id), "source": row.source, "medium": row.medium,
        "first_touch": row.first_touch, "last_touch": row.last_touch,
        "campaign_id": str(row.campaign_id) if row.campaign_id else None,
    }


# ---------- content engine (§42) ----------
class ContentGenerateIn(BaseModel):
    kind: str
    brief: str
    language: str = "ar"


class ContentReviewIn(BaseModel):
    decision: str  # approved | rejected
    notes: str | None = None


@router.post("/content/generate", status_code=201)
async def post_generate(
    body: ContentGenerateIn,
    auth: AuthContext = Depends(require(MARKETING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    asset = await generate_content(
        session, tenant_id=auth.tenant_id, kind=body.kind, brief=body.brief,
        language=body.language, actor_id=auth.user_id,
    )
    return {"id": str(asset.id), "status": asset.status, "content": asset.content}


@router.post("/content/{asset_id}/review")
async def post_review(
    asset_id: uuid.UUID,
    body: ContentReviewIn,
    auth: AuthContext = Depends(require(MARKETING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    asset = await review_content(
        session, tenant_id=auth.tenant_id, asset_id=asset_id, reviewer_id=auth.user_id,
        decision=body.decision, notes=body.notes,
    )
    return {"id": str(asset.id), "status": asset.status}


@router.post("/content/{asset_id}/publish")
async def post_publish(
    asset_id: uuid.UUID,
    auth: AuthContext = Depends(require(MARKETING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    asset = await publish_content(session, tenant_id=auth.tenant_id, asset_id=asset_id,
                                  actor_id=auth.user_id)
    return {"id": str(asset.id), "status": asset.status}


@router.get("/content")
async def list_content(
    auth: AuthContext = Depends(require(MARKETING_READ)),
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
) -> list[dict[str, Any]]:
    from app.marketing.models_ext import MarketingAsset

    query = select(MarketingAsset).where(MarketingAsset.tenant_id == auth.tenant_id)
    if status:
        query = query.where(MarketingAsset.status == status)
    rows = (
        await session.execute(query.order_by(MarketingAsset.created_at.desc()).limit(50))
    ).scalars().all()
    return [
        {"id": str(a.id), "kind": a.kind, "status": a.status, "language": a.language,
         "content": a.content[:200]}
        for a in rows
    ]
