"""Lead event handlers: auto-qualification pipeline hooks (M2 scope)."""

from __future__ import annotations

import uuid

from app.events.registry import event_handler


@event_handler("lead.created")
async def on_lead_created_sla(session, envelope):  # noqa: ANN001
    """Start SLA tracking for the new lead (now handled by Decision Plane)."""
    payload = envelope["payload"]
    if not payload.get("lead_id") or not envelope.get("tenant_id"):
        return
    from app.automation.service import start_sla_tracker

    tenant_id = uuid.UUID(envelope["tenant_id"])
    await start_sla_tracker(
        session, tenant_id=tenant_id, entity_type="lead",
        entity_id=uuid.UUID(payload["lead_id"]), trigger_event="lead.created",
    )


@event_handler("lead.qualified")
async def on_lead_qualified(session, envelope):  # noqa: ANN001
    """Qualified leads enter the matching pipeline (M4 handler registers here)."""
    return None
