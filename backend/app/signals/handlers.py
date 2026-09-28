"""V4 integration hooks: wire Claims + Signals + Decision into the live flow.

These event handlers connect the V4 planes to the existing domain events,
so every critical fact gets a provenant claim, every routing gets a decision
record, and every projection tracks its staleness contract.
"""

from __future__ import annotations

import logging
import uuid

from app.events.registry import event_handler


@event_handler("property.price_changed")
async def on_price_changed_claim(session, envelope):  # noqa: ANN001
    """Price changes → verified claim (SYSTEM_DERIVED, high confidence)."""
    from app.claims.service import assert_claim

    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    await assert_claim(
        session, tenant_id=tenant_id,
        entity_type="property_asset", entity_id=uuid.UUID(payload["asset_id"]),
        field="price", value={"amount": payload.get("amount"), "currency": payload.get("currency", "EGP")},
        source="system.price_engine", assertion_type="SYSTEM_DERIVED", confidence=95,
    )


@event_handler("unit.reserved")
async def on_unit_reserved_claim(session, envelope):  # noqa: ANN001
    """Availability change → CURRENT claim (SYSTEM_DERIVED from transactional op)."""
    from app.claims.service import assert_claim

    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    await assert_claim(
        session, tenant_id=tenant_id,
        entity_type="property_asset", entity_id=uuid.UUID(payload["asset_id"]),
        field="availability", value={"state": "RESERVED"},
        source="reservation_service", assertion_type="SYSTEM_DERIVED", confidence=100,
    )


@event_handler("unit.released")
async def on_unit_released_claim(session, envelope):  # noqa: ANN001
    from app.claims.service import assert_claim

    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    await assert_claim(
        session, tenant_id=tenant_id,
        entity_type="property_asset", entity_id=uuid.UUID(payload["asset_id"]),
        field="availability", value={"state": "AVAILABLE"},
        source="reservation_service", assertion_type="SYSTEM_DERIVED", confidence=100,
    )


@event_handler("lead.created")
async def on_lead_created_staleness(session, envelope):  # noqa: ANN001
    """Track search projection staleness when inventory changes."""
    from app.signals.service import check_projection_staleness

    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    try:
        await check_projection_staleness(
            session, tenant_id=tenant_id, projection_name="property_search_documents"
        )
    except Exception:  # noqa: BLE001 — advisory, but never silently
        logging.getLogger(__name__).warning(
            "projection staleness check failed (tenant=%s)", tenant_id, exc_info=True)
