"""M4 Matching + M5 Sales tests — the golden vertical (§113, §115)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.db import session_factory
from app.core.errors import Conflict, ValidationFailed
from app.identity.models import Person
from app.leads.models import Lead, LeadRequirement
from app.matching.service import build_search_document, embed_lead, run_matching
from app.properties.models import UnitInventory
from app.properties.service import create_asset, create_project, set_price
from app.sales.models import Reservation
from app.sales.service import (
    create_offer,
    create_opportunity,
    create_reservation,
    create_viewing,
    transition_offer,
    transition_reservation,
    transition_viewing,
)


async def _seed_inventory(db, tenant, *, n_units=3, base_price="4000000", area="New Cairo"):
    """Creates a project with n unit assets, prices, search docs."""
    async with db.begin():
        project = await create_project(db, tenant_id=tenant.id, name="Seed Compound",
                                       location={"city": "Cairo", "area": area})
        assets = []
        for i in range(n_units):
            price = str(int(base_price) + i * 500000)
            asset = await create_asset(
                db, tenant_id=tenant.id,
                title=f"Unit {i+1}", property_type="apartment", asset_type="unit",
                project_id=project.id, bedrooms=3, bathrooms=2,
                area_value=Decimal("120") + i * 10, finishing="finished",
                delivery_status="ready", location={"city": "Cairo", "area": area},
            )
            await set_price(db, tenant_id=tenant.id, asset_id=asset.id, amount=price)
            await build_search_document(db, tenant_id=tenant.id, asset_id=asset.id)
            assets.append(asset)
    return project, assets


async def _lead_with_requirements(db, tenant, *, bedrooms=3, max_budget="4200000", areas=None):
    async with db.begin():
        person = Person(tenant_id=tenant.id, full_name="عميل الاختبار", phone="+201200000001")
        db.add(person)
        await db.flush()
        lead = Lead(tenant_id=tenant.id, person_id=person.id, source="test")
        db.add(lead)
        await db.flush()
        db.add(
            LeadRequirement(
                tenant_id=tenant.id, lead_id=lead.id,
                explicit={"bedrooms": bedrooms, "max_budget": max_budget,
                          "areas": areas or ["new cairo"], "property_types": ["apartment"],
                          "delivery_preference": "ready"},
                timeline="3_months", confidence=85,
            )
        )
    return lead


async def test_matching_pipeline_hard_filters_and_ranking(db, tenant, owner_ctx):
    _, assets = await _seed_inventory(db, tenant, n_units=3)
    lead = await _lead_with_requirements(db, tenant, max_budget="4200000")

    async with db.begin():
        await embed_lead(db, tenant_id=tenant.id, lead_id=lead.id)
        run = await run_matching(db, tenant_id=tenant.id, lead_id=lead.id)
    assert run.candidates_considered == 3
    # hard filter: budget 4.2M × 1.1 tolerance excludes the 5.0M unit — by design
    assert len(run.results) == 2
    # ranked: cheapest first (budget fit), all within hard filter (10% tolerance)
    scores = [r["final_score"] for r in run.results]
    assert scores == sorted(scores, reverse=True), "results must be ranked descending"
    assert run.results[0]["reasons"]
    assert all(r["asset_id"] != str(assets[-1].id) for r in run.results)


async def test_matching_filters_out_reserved_units(db, tenant, owner_ctx):
    _, assets = await _seed_inventory(db, tenant, n_units=2)
    lead = await _lead_with_requirements(db, tenant)
    # reserve one unit via inventory
    from app.properties.service import mark_reserved

    async with db.begin():
        await mark_reserved(db, tenant_id=tenant.id, asset_id=assets[0].id,
                            reservation_id=uuid.uuid4())
        await build_search_document(db, tenant_id=tenant.id, asset_id=assets[0].id)
        run = await run_matching(db, tenant_id=tenant.id, lead_id=lead.id)
    matched_ids = [r["asset_id"] for r in run.results]
    assert str(assets[0].id) not in matched_ids, "RESERVED units must never match (§102)"
    assert str(assets[1].id) in matched_ids


async def test_full_golden_flow_lead_to_reservation(db, tenant, owner_ctx):
    """§113: Lead → Opportunity → Viewing → Offer → Reservation, with events."""
    _, assets = await _seed_inventory(db, tenant, n_units=2)
    lead = await _lead_with_requirements(db, tenant)

    # Opportunity (auto-converts the lead)
    async with db.begin():
        opp = await create_opportunity(db, tenant_id=tenant.id, lead_id=lead.id,
                                       person_id=lead.person_id, actor_id=owner_ctx.user_id)
        lead_row = await db.get(Lead, lead.id)
        assert lead_row.lifecycle_stage == "CONVERTED"
        opp_id = opp.id

    # Viewing with timezone-aware scheduling
    async with db.begin():
        viewing = await create_viewing(
            db, tenant_id=tenant.id, opportunity_id=opp_id, asset_id=assets[0].id,
            scheduled_at=datetime.now(UTC) + timedelta(days=2),
            salesperson_id=owner_ctx.user_id, actor_id=owner_ctx.user_id,
        )
        viewing_id = viewing.id
    assert viewing.scheduled_at.tzinfo is not None

    # naive datetime rejected (review rule: no tz-less times)
    async with db.begin():
        with pytest.raises(ValidationFailed):
            await create_viewing(
                db, tenant_id=tenant.id, opportunity_id=opp_id, asset_id=assets[0].id,
                scheduled_at=datetime(2026, 10, 1, 15, 0),  # naive!
            )

    # Viewing lifecycle
    async with db.begin():
        await transition_viewing(db, viewing=viewing, event="confirm", actor_id=owner_ctx.user_id)
        assert viewing.status == "CONFIRMED"
        await transition_viewing(db, viewing=viewing, event="attend", actor_id=owner_ctx.user_id)
        await transition_viewing(db, viewing=viewing, event="complete",
                                 feedback={"interest": "high"})
        assert viewing.status == "COMPLETED"

    # Offer within budget → no approval needed
    async with db.begin():
        offer = await create_offer(
            db, tenant_id=tenant.id, opportunity_id=opp_id, asset_id=assets[0].id,
            price_amount=Decimal("3950000"), actor_id=owner_ctx.user_id,
        )
        assert offer.approval_required is False
        offer_id = offer.id
        await transition_offer(db, offer=offer, event="send", actor_id=owner_ctx.user_id)
        await transition_offer(db, offer=offer, event="accept", actor_id=owner_ctx.user_id)

    # Reservation (transactional) — inventory flips to RESERVED
    async with db.begin():
        reservation = await create_reservation(
            db, tenant_id=tenant.id, opportunity_id=opp_id, offer_id=offer_id,
            idempotency_key="res-golden-1", actor_id=owner_ctx.user_id,
        )
        inv = (
            await db.execute(select(UnitInventory).where(UnitInventory.asset_id == assets[0].id))
        ).scalar_one()
        assert inv.state == "RESERVED"
        assert inv.reservation_id == reservation.id
        res_id = reservation.id

    # Idempotent replay: same key → same reservation, no duplicates
    async with db.begin():
        res2 = await create_reservation(
            db, tenant_id=tenant.id, opportunity_id=opp_id, offer_id=offer_id,
            idempotency_key="res-golden-1", actor_id=owner_ctx.user_id,
        )
        assert res2.id == res_id
        count = (
            await db.execute(select(func.count()).select_from(Reservation))
        ).scalar_one()
        assert count == 1

    # Second reservation for a DIFFERENT offer on the SAME unit → impossible
    async with db.begin():
        offer2 = await create_offer(
            db, tenant_id=tenant.id, opportunity_id=opp_id, asset_id=assets[0].id,
            price_amount=Decimal("3900000"), actor_id=owner_ctx.user_id,
        )
        offer2_id = offer2.id
        await transition_offer(db, offer=offer2, event="send", actor_id=owner_ctx.user_id)
        await transition_offer(db, offer=offer2, event="accept", actor_id=owner_ctx.user_id)
    async with db.begin():
        with pytest.raises(Conflict):
            await create_reservation(
                db, tenant_id=tenant.id, opportunity_id=opp_id, offer_id=offer2_id,
                idempotency_key="res-golden-2", actor_id=owner_ctx.user_id,
            )

    # Cancel reservation → inventory returns to AVAILABLE
    async with db.begin():
        res = await db.get(Reservation, res_id)
        await transition_reservation(db, reservation=res, event="cancel",
                                     actor_id=owner_ctx.user_id, reason="customer withdrew")
    async with db.begin():
        inv = (
            await db.execute(select(UnitInventory).where(UnitInventory.asset_id == assets[0].id))
        ).scalar_one()
        assert inv.state == "AVAILABLE"


async def test_discount_above_threshold_requires_approval(db, tenant, owner_ctx):
    _, assets = await _seed_inventory(db, tenant, n_units=1, base_price="4000000")
    lead = await _lead_with_requirements(db, tenant)
    async with db.begin():
        opp = await create_opportunity(db, tenant_id=tenant.id, lead_id=lead.id,
                                       person_id=lead.person_id)
        # 20% discount → sales role gets blocked
        with pytest.raises(ValidationFailed):
            await create_offer(
                db, tenant_id=tenant.id, opportunity_id=opp.id, asset_id=assets[0].id,
                price_amount=Decimal("3200000"), actor_id=owner_ctx.user_id,
                actor_role="sales",
            )
        # manager can create it, but approval is flagged
        offer = await create_offer(
            db, tenant_id=tenant.id, opportunity_id=opp.id, asset_id=assets[0].id,
            price_amount=Decimal("3200000"), actor_id=owner_ctx.user_id,
            actor_role="sales_manager",
        )
        assert offer.approval_required is True
        # cannot send unapproved high-discount offer per policy: sending works but
        # acceptance path requires the approval event first
        await transition_offer(db, offer=offer, event="send", actor_id=owner_ctx.user_id)
        assert offer.status == "SENT"


async def test_concurrent_reservations_exactly_one_wins(db, tenant, owner_ctx):
    """§102 + review rule #2: two parallel reservation attempts on one unit."""
    _, assets = await _seed_inventory(db, tenant, n_units=1)
    lead = await _lead_with_requirements(db, tenant)
    async with db.begin():
        opp = await create_opportunity(db, tenant_id=tenant.id, lead_id=lead.id,
                                       person_id=lead.person_id)
        opp_id = opp.id
        offer = await create_offer(
            db, tenant_id=tenant.id, opportunity_id=opp_id, asset_id=assets[0].id,
            price_amount=Decimal("4000000"), actor_id=owner_ctx.user_id,
        )
        await transition_offer(db, offer=offer, event="send", actor_id=owner_ctx.user_id)
        await transition_offer(db, offer=offer, event="accept", actor_id=owner_ctx.user_id)
        offer_id = offer.id

    import asyncio

    results, errors = [], []

    async def attempt(n: int):
        try:
            async with session_factory() as s:
                async with s.begin():
                    res = await create_reservation(
                        s, tenant_id=tenant.id, opportunity_id=opp_id, offer_id=offer_id,
                        idempotency_key=f"concurrent-{n}", actor_id=owner_ctx.user_id,
                    )
                    results.append(res.id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    await asyncio.gather(attempt(1), attempt(2))
    assert len(results) == 1, "exactly one reservation may win"
    assert len(errors) == 1, "the other must conflict"
    inv = (
        await db.execute(select(UnitInventory).where(UnitInventory.asset_id == assets[0].id))
    ).scalar_one()
    assert inv.state == "RESERVED"


async def test_viewing_slot_conflict_detection(db, tenant, owner_ctx):
    _, assets = await _seed_inventory(db, tenant, n_units=2)
    lead = await _lead_with_requirements(db, tenant)
    async with db.begin():
        opp = await create_opportunity(db, tenant_id=tenant.id, lead_id=lead.id,
                                       person_id=lead.person_id)
        opp_id = opp.id
        slot = datetime.now(UTC) + timedelta(days=3)
        v1 = await create_viewing(
            db, tenant_id=tenant.id, opportunity_id=opp_id, asset_id=assets[0].id,
            scheduled_at=slot, salesperson_id=owner_ctx.user_id,
        )
        with pytest.raises(Conflict):
            await create_viewing(
                db, tenant_id=tenant.id, opportunity_id=opp_id, asset_id=assets[1].id,
                scheduled_at=slot + timedelta(minutes=15),
                salesperson_id=owner_ctx.user_id,
            )
        # different salesperson → fine
        v2 = await create_viewing(
            db, tenant_id=tenant.id, opportunity_id=opp_id, asset_id=assets[1].id,
            scheduled_at=slot + timedelta(minutes=15), salesperson_id=None,
        )
    assert v2.id != v1.id
