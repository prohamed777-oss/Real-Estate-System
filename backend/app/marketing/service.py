"""Marketing & acquisition services (§40-42): attribution capture + content engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.events.outbox import emit
from app.marketing.models import Attribution, Campaign, LeadSource
from app.marketing.models_ext import MarketingAsset


async def capture_attribution(
    session: AsyncSession, *, tenant_id: uuid.UUID, lead_id: uuid.UUID,
    source: str | None = None, medium: str | None = None, campaign_id: uuid.UUID | None = None,
    content: str | None = None, landing_page: str | None = None, referrer: str | None = None,
    utm: dict[str, Any] | None = None,
) -> Attribution:
    """First-touch/last-touch capture (§41). Idempotent per lead."""
    existing = (
        await session.execute(
            select(Attribution).where(
                Attribution.tenant_id == tenant_id, Attribution.lead_id == lead_id
            )
        )
    ).scalar_one_or_none()
    touch = {
        "source": source, "medium": medium, "content": content,
        "landing_page": landing_page, "referrer": referrer, "utm": utm or {},
        "at": datetime.now(UTC).isoformat(),
    }
    if existing is not None:
        existing.last_touch = touch  # last touch updates; first touch immutable
        await session.flush()
        return existing
    row = Attribution(
        tenant_id=tenant_id, lead_id=lead_id, first_touch=touch, last_touch=touch,
        source=source, medium=medium, campaign_id=campaign_id, content=content,
        landing_page=landing_page, referrer=referrer,
    )
    session.add(row)
    await session.flush()
    return row


async def create_campaign(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str, channel: str | None = None,
    budget: float | None = None, currency: str = "EGP", utm: dict[str, Any] | None = None,
    actor_id=None,
) -> Campaign:
    campaign = Campaign(
        tenant_id=tenant_id, name=name, channel=channel, budget=budget,
        currency=currency, utm=utm or {}, status="active",
    )
    session.add(campaign)
    await session.flush()
    await audit(session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
                action="campaign.created", entity_type="campaign", entity_id=campaign.id,
                after={"name": name})
    return campaign


async def upsert_lead_source(
    session: AsyncSession, *, tenant_id: uuid.UUID, key: str, name: str,
    channel: str | None = None, campaign_id: uuid.UUID | None = None,
) -> LeadSource:
    row = (
        await session.execute(
            select(LeadSource).where(
                LeadSource.tenant_id == tenant_id, LeadSource.key == key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = LeadSource(tenant_id=tenant_id, key=key, name=name, channel=channel,
                          campaign_id=campaign_id)
        session.add(row)
    else:
        row.name = name
        row.channel = channel
        row.campaign_id = campaign_id
    await session.flush()
    return row


# ---------- content engine (§42): Generate → Review → Approve → Publish ----------
CONTENT_KINDS = {"listing_title", "listing_description", "whatsapp_copy", "instagram_caption",
                 "ad_copy", "email_copy", "video_script"}


async def generate_content(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: str, brief: str,
    language: str = "ar", actor_id=None,
) -> MarketingAsset:
    """AI drafts content; it is born as DRAFT — never publishable directly (§42)."""
    if kind not in CONTENT_KINDS:
        raise ValidationFailed(f"Unknown content kind: {kind}")
    from app.ai.gateway import get_model_provider

    provider = get_model_provider()
    prompt = (
        f"اكتب {kind.replace('_', ' ')} عقاري احترافي بالعربية الفصحى المبسطة.\n"
        f"الموجز: {brief}\n"
        "ممنوع اختلاس أسعار أو وعود مضمونة. أخرج النص فقط."
    ) if language == "ar" else (
        f"Write a professional real estate {kind.replace('_', ' ')}.\n"
        f"Brief: {brief}\nNo invented prices, no guarantees. Output the copy only."
    )
    response = await provider.chat(
        [{"role": "system", "content": "أنت كاتب محتوى عقاري خبير."},
         {"role": "user", "content": prompt}],
        None, "standard",
    )
    asset = MarketingAsset(
        tenant_id=tenant_id, kind=kind, language=language, brief=brief,
        content=response.text or "", status="draft", created_by=str(actor_id) if actor_id else None,
        model=provider.provider,
    )
    session.add(asset)
    await session.flush()
    await audit(session, tenant_id=tenant_id, actor_type="agent",
                actor_id="content-engine", action="content.generated",
                entity_type="marketing_asset", entity_id=asset.id, source="ai",
                after={"kind": kind})
    return asset


async def review_content(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID,
    reviewer_id: uuid.UUID, decision: str, notes: str | None = None,
) -> MarketingAsset:
    asset = await session.get(MarketingAsset, asset_id)
    if asset is None or asset.tenant_id != tenant_id:
        raise NotFound("Content asset not found")
    if asset.status != "draft":
        raise Conflict("Only draft content can be reviewed")
    if decision not in ("approved", "rejected"):
        raise ValidationFailed("decision must be approved|rejected")
    asset.status = "approved" if decision == "approved" else "rejected"
    asset.reviewer_id = reviewer_id
    asset.review_notes = notes
    asset.reviewed_at = datetime.now(UTC)
    await audit(session, tenant_id=tenant_id, actor_type="user", actor_id=reviewer_id,
                action=f"content.{decision}", entity_type="marketing_asset", entity_id=asset.id)
    return asset


async def publish_content(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID, actor_id=None,
) -> MarketingAsset:
    asset = await session.get(MarketingAsset, asset_id)
    if asset is None or asset.tenant_id != tenant_id:
        raise NotFound("Content asset not found")
    if asset.status != "approved":
        raise Conflict("Publishing requires human approval (§42)")
    asset.status = "published"
    asset.published_at = datetime.now(UTC)
    await emit(
        session, event_name="content.published", tenant_id=tenant_id,
        aggregate_type="marketing_asset", aggregate_id=asset.id,
        payload={"asset_id": str(asset.id), "kind": asset.kind},
    )
    return asset
