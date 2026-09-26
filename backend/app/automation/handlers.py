"""Automation event handlers: rules engine + SLA start on domain events."""

from __future__ import annotations

import uuid

from app.events.registry import event_handler


@event_handler("lead.created")
async def on_lead_created(session, envelope):  # noqa: ANN001
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from app.automation.service import apply_rules_for_event, start_sla_tracker

    payload = envelope["payload"]
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="lead.created", payload=payload
    )
    await start_sla_tracker(
        session, tenant_id=tenant_id, entity_type="lead",
        entity_id=uuid.UUID(payload["lead_id"]), trigger_event="lead.created",
    )


@event_handler("lead.qualified")
async def on_lead_qualified(session, envelope):  # noqa: ANN001
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from app.automation.service import apply_rules_for_event, resolve_sla

    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="lead.qualified", payload=envelope["payload"]
    )
    lead_id = envelope["payload"].get("lead_id")
    if lead_id:
        await resolve_sla(session, entity_type="lead", entity_id=uuid.UUID(lead_id), met=True)


@event_handler("message.received")
async def on_message_received(session, envelope):  # noqa: ANN001
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from app.automation.service import apply_rules_for_event, start_sla_tracker

    payload = envelope["payload"]
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="message.received", payload=payload
    )
    if payload.get("conversation_id"):
        await start_sla_tracker(
            session, tenant_id=tenant_id, entity_type="conversation",
            entity_id=uuid.UUID(payload["conversation_id"]),
            trigger_event="message.received",
        )


@event_handler("viewing.no_show")
async def on_viewing_no_show(session, envelope):  # noqa: ANN001
    """No-show → recovery journey (§112)."""
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from app.automation.service import apply_rules_for_event

    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="viewing.no_show", payload=envelope["payload"]
    )


@event_handler("payment.failed")
async def on_payment_failed(session, envelope):  # noqa: ANN001
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from app.automation.service import apply_rules_for_event

    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="payment.failed", payload=envelope["payload"]
    )


@event_handler("deal.closed")
async def on_deal_closed(session, envelope):  # noqa: ANN001
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from app.automation.service import apply_rules_for_event

    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="deal.closed", payload=envelope["payload"]
    )
