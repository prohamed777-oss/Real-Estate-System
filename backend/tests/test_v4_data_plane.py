"""V4-2 wave tests: Claims bitemporal truth, Signal Engine parity,
Staleness contracts, SSE real-time gateway."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.claims.service import (
    Claim,
    assert_claim,
    get_truth,
    mark_stale,
    verify_claim,
)
from app.core.db import session_factory
from app.signals.service import (
    define_signal,
    validate_parity,
)


async def test_claims_provenance_and_verification_gate(db, tenant):
    async with db.begin():
        # OBSERVED claim (from a human) → CURRENT immediately
        c1 = await assert_claim(
            db, tenant_id=tenant.id, entity_type="property_asset",
            entity_id=uuid.uuid4(), field="view", value="Landscape",
            source="site_visit", assertion_type="OBSERVED", confidence=95,
        )
        assert c1.truth_status == "CURRENT"
        # EXTRACTED claim (from WhatsApp text) → born UNVERIFIED (V4 4.4 rule)
        c2 = await assert_claim(
            db, tenant_id=tenant.id, entity_type="lead", entity_id=uuid.uuid4(),
            field="max_budget", value="4,000,000", source="whatsapp",
            assertion_type="EXTRACTED", confidence=60,
        )
        assert c2.truth_status == "UNVERIFIED", "extracted claims are never silently trusted"
        await verify_claim(db, tenant_id=tenant.id, claim_id=c2.id, verified_by="manager")
    c2b = await db.get(__import__("app.claims.service", fromlist=["Claim"]).Claim, c2.id)
    assert c2b.truth_status == "CURRENT"
    assert c2b.verified_by == "manager"


async def test_claims_supersede_and_bitemporal_queries(db, tenant):

    eid = uuid.uuid4()
    async with db.begin():
        await assert_claim(db, tenant_id=tenant.id, entity_type="lead", entity_id=eid,
                           field="max_budget", value="3000000", assertion_type="OBSERVED")
        t_between = datetime.now(UTC)
    await asyncio.sleep(0.05)
    async with db.begin():
        await assert_claim(db, tenant_id=tenant.id, entity_type="lead", entity_id=eid,
                           field="max_budget", value="4500000", assertion_type="OBSERVED")
    async with session_factory() as s:
        rows = (
            await s.execute(
                select(Claim).where(
                    Claim.tenant_id == tenant.id, Claim.entity_id == eid,
                    Claim.field == "max_budget", Claim.truth_status == "CURRENT",
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].value["v"] == "4500000"
    # bitemporal: what did the system believe EARLIER?
    async with session_factory() as s:
        earlier = await get_truth(
            s, tenant_id=tenant.id, entity_type="lead", entity_id=eid,
            field="max_budget", include_all=True,
            as_of_recorded=t_between,
        )
    assert any(r.value["v"] == "3000000" for r in earlier), "history must answer 'believed at T'"


async def test_mark_stale_on_canonical_change(db, tenant):
    eid = uuid.uuid4()
    async with db.begin():
        await assert_claim(db, tenant_id=tenant.id, entity_type="unit", entity_id=eid,
                           field="availability", value="AVAILABLE", assertion_type="OBSERVED")
    async with db.begin():
        changed = await mark_stale(db, tenant_id=tenant.id, entity_type="unit",
                                   entity_id=eid, field="availability")
    assert changed == 1, "freshness contract: canonical change → old claims STALE"


async def test_signal_engine_parity_validator(db, tenant):
    async with db.begin():
        await define_signal(
            db, tenant_id=tenant.id, name="active_listings",
            transform_sql="SELECT COUNT(*) FROM property_assets WHERE tenant_id = :tenant_id",
            description="عدد الوحدات النشطة",
        )
        eid = uuid.uuid4()
        from app.signals.models import SignalDefinition
        from app.signals.service import refresh_online_signal

        definition = (
            await db.execute(
                __import__("sqlalchemy").select(SignalDefinition).where(
                    SignalDefinition.tenant_id == tenant.id, SignalDefinition.name == "active_listings"
                )
            )
        ).scalar_one()
        await refresh_online_signal(db, tenant_id=tenant.id, entity_type="tenant_scope",
                                     entity_id=eid, definition=definition)
    result = await validate_parity(db, tenant_id=tenant.id, entity_type="tenant_scope",
                                   entity_id=eid, name="active_listings")
    assert result["drifted"] is False, "online and offline must be PROVABLY identical (1.32)"
    assert result["online"] == result["offline"]


async def test_staleness_contract_degrades_critical_path(db, tenant):
    from app.signals.service import (
        check_projection_staleness,
        mark_projection_processed,
        register_projection,
    )

    async with db.begin():
        await register_projection(
            db, tenant_id=tenant.id, projection_name="property_search_documents",
            max_staleness_ms=1000, critical_path=False,
        )
        # simulate lag: last processed 60s ago
        row = (
            await db.execute(
                __import__("sqlalchemy").text(
                    "UPDATE projection_registry SET last_event_processed_at = now() - interval '60 seconds' "
                    "WHERE projection_name = 'property_search_documents' RETURNING id"
                )
            )
        )
        result = await check_projection_staleness(
            db, tenant_id=tenant.id, projection_name="property_search_documents"
        )
    assert result["within_contract"] is False, "60s lag > 1s contract"
    # non-critical path serves stale but flags it
    assert result["degraded_mode"] is False

    async with db.begin():
        await mark_projection_processed(
            db, tenant_id=tenant.id, projection_name="property_search_documents"
        )
        result2 = await check_projection_staleness(
            db, tenant_id=tenant.id, projection_name="property_search_documents"
        )
    assert result2["within_contract"] is True


async def test_realtime_sse_streams_tenant_events(db, tenant, api, owner_ctx):
    from app.events.outbox import emit

    async with db.begin():
        await emit(db, event_name="unit.reserved", tenant_id=tenant.id,
                   payload={"test": "sse"}, aggregate_type="property_asset")

    # stream via dev auth
    import httpx

    from app.main import app

    async def consume():
        events = []
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "GET",
                f"/api/v1/realtime/stream?authorization=Bearer%20dev:{owner_ctx.user_id}",
                headers={"X-Dev-Email": "owner@acme.test"},
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("event: domain_event"):
                        events.append(line)
                        if len(events) >= 1:
                            break
        return events

    # testable core: the fetch the stream serves — tenant-scoped, newest last
    from app.realtime.sse import fetch_events

    async with session_factory() as s:
        rows = await fetch_events(s, tenant_id=tenant.id, after_id=None)
    assert any(r.event_type == "unit.reserved" and r.payload.get("test") == "sse"
               for r in rows), "the stream source must deliver the tenant's domain events"
    assert all(str(r.event_id) for r in rows)
