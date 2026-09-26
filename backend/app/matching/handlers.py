"""Matching event/job handlers: keep search docs + embeddings fresh."""

from __future__ import annotations

import uuid

from app.events.registry import event_handler, job_handler


@event_handler("property.created")
async def on_property_created(session, envelope):  # noqa: ANN001
    from app.matching.service import build_search_document

    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    await build_search_document(session, tenant_id=tenant_id,
                                asset_id=uuid.UUID(payload["asset_id"]))


@event_handler("property.price_changed")
async def on_price_changed(session, envelope):  # noqa: ANN001
    from app.matching.service import build_search_document

    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    await build_search_document(session, tenant_id=tenant_id,
                                asset_id=uuid.UUID(payload["asset_id"]))


@event_handler("unit.reserved")
async def on_unit_reserved(session, envelope):  # noqa: ANN001
    from app.matching.service import build_search_document

    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    await build_search_document(session, tenant_id=tenant_id,
                                asset_id=uuid.UUID(payload["asset_id"]))


@event_handler("unit.released")
async def on_unit_released(session, envelope):  # noqa: ANN001
    from app.matching.service import build_search_document

    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    await build_search_document(session, tenant_id=tenant_id,
                                asset_id=uuid.UUID(payload["asset_id"]))


@event_handler("lead.requirements_updated")
async def on_requirements_updated(session, envelope):  # noqa: ANN001
    """New requirements → run matching automatically (§112 automation hook)."""
    from app.matching.service import embed_lead, run_matching

    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    lead_id = uuid.UUID(payload["lead_id"])
    await embed_lead(session, tenant_id=tenant_id, lead_id=lead_id)
    await run_matching(session, tenant_id=tenant_id, lead_id=lead_id, top_n=10)


@job_handler("matching.embed_documents")
async def embed_documents_job(session, tenant_id, payload):  # noqa: ANN001
    from app.matching.service import embed_search_documents

    if tenant_id is None:
        return
    await embed_search_documents(session, tenant_id=tenant_id)
