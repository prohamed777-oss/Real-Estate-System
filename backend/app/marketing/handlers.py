"""Marketing handlers: attribution capture on lead creation (§41)."""

from __future__ import annotations

import uuid

from app.events.registry import event_handler


@event_handler("lead.created")
async def on_lead_created_capture_attribution(session, envelope):  # noqa: ANN001
    """Capture first-touch attribution from the lead's source/campaign (§41)."""
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from sqlalchemy import select

    from app.marketing.models import Attribution
    from app.marketing.service import capture_attribution

    payload = envelope["payload"]
    lead_id = uuid.UUID(payload["lead_id"])
    existing = (
        await session.execute(
            select(Attribution).where(
                Attribution.tenant_id == tenant_id, Attribution.lead_id == lead_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    source = payload.get("source")
    campaign_id = payload.get("campaign_id")
    await capture_attribution(
        session, tenant_id=tenant_id, lead_id=lead_id,
        source=source or payload.get("channel") or "direct",
        medium=payload.get("medium"),
        campaign_id=uuid.UUID(campaign_id) if campaign_id else None,
        utm=payload.get("utm"),
    )
