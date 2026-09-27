"""M3 Property & Supply tests — including REAL concurrency tests (review rule #2).

The double-hold test runs two transactions in parallel against real PostgreSQL
with FOR UPDATE locking. One MUST win, one MUST get Conflict.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.db import session_factory
from app.core.errors import Conflict
from app.listings.models import Listing
from app.properties.models import (
    Building,
    InventoryHold,
    PriceVersion,
    Project,
    PropertyAsset,
    UnitInventory,
)
from app.properties.service import (
    check_availability,
    create_asset,
    create_building,
    create_listing,
    create_mandate,
    create_payment_plan,
    create_project,
    hold_unit,
    release_unit,
    set_price,
    transition_listing,
)


async def _make_unit(db, tenant, *, asset_type="unit", project_id=None):
    async with db.begin():
        if asset_type == "unit" and project_id is None:
            project = await create_project(
                db, tenant_id=tenant.id, name="Test Compound",
                location={"city": "New Cairo"},
            )
            project_id = project.id
        asset = await create_asset(
            db, tenant_id=tenant.id, title="شقة 120م التجمع الخامس",
            property_type="apartment", asset_type=asset_type, project_id=project_id,
            bedrooms=3, bathrooms=2, area_value=Decimal("120"),
            finishing="finished", delivery_status="ready",
            location={"city": "Cairo", "area": "New Cairo"},
        )
    return asset


async def test_project_building_unit_hierarchy(db, tenant, owner_ctx):
    async with db.begin():
        project = await create_project(
            db, tenant_id=tenant.id, name="ماونتن فيو هايد بارك",
            location={"city": "New Cairo", "area": "Hyde Park"}, actor_id=owner_ctx.user_id,
        )
        building = await create_building(db, tenant_id=tenant.id, project_id=project.id,
                                         name="B2", floors=10)
        asset = await create_asset(
            db, tenant_id=tenant.id, title="Unit B2-0303", property_type="apartment",
            asset_type="unit", project_id=project.id, building_id=building.id,
        )
    assert asset.asset_type == "unit"
    inv = (
        await db.execute(select(UnitInventory).where(UnitInventory.asset_id == asset.id))
    ).scalar_one()
    assert inv.state == "AVAILABLE"


async def test_price_versioning_never_deletes_history(db, tenant, owner_ctx):
    asset = await _make_unit(db, tenant)
    async with db.begin():
        v1 = await set_price(db, tenant_id=tenant.id, asset_id=asset.id, amount="4000000",
                             actor_id=owner_ctx.user_id)
    async with db.begin():
        v2 = await set_price(db, tenant_id=tenant.id, asset_id=asset.id, amount="4250000",
                             actor_id=owner_ctx.user_id)
    async with db.begin():
        history = (
            await db.execute(
                select(PriceVersion)
                .where(PriceVersion.asset_id == asset.id)
                .order_by(PriceVersion.valid_from)
            )
        ).scalars().all()
    assert len(history) == 2, "price history must be append-only"
    assert history[0].id == v1.id and history[0].valid_to is not None  # closed, not deleted
    assert history[1].id == v2.id and history[1].valid_to is None  # current
    # same price again → no new version
    async with db.begin():
        v3 = await set_price(db, tenant_id=tenant.id, asset_id=asset.id, amount="4250000")
    assert v3.id == v2.id


async def test_hold_and_release_flow(db, tenant, owner_ctx):
    asset = await _make_unit(db, tenant)
    async with db.begin():
        hold = await hold_unit(db, tenant_id=tenant.id, asset_id=asset.id,
                               created_by=str(owner_ctx.user_id))
        inv = (
            await db.execute(select(UnitInventory).where(UnitInventory.asset_id == asset.id))
        ).scalar_one()
        assert inv.state == "HELD"
        assert inv.hold_id == hold.id
    async with db.begin():
        await release_unit(db, tenant_id=tenant.id, asset_id=asset.id)
        inv = (
            await db.execute(select(UnitInventory).where(UnitInventory.asset_id == asset.id))
        ).scalar_one()
        assert inv.state == "AVAILABLE"


async def test_concurrent_holds_exactly_one_wins(db, tenant, owner_ctx):
    """§102 invariant: two concurrent holds on one unit → exactly one succeeds."""
    asset = await _make_unit(db, tenant)

    async def attempt():
        async with session_factory() as s:
            async with s.begin():
                return await hold_unit(s, tenant_id=tenant.id, asset_id=asset.id,
                                       created_by="concurrent-test")

    results = []
    errors = []
    import asyncio

    async def guarded():
        try:
            results.append(await attempt())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    pass
    await asyncio.gather(guarded(), guarded())

    assert len(results) + len(errors) == 2
    assert len(results) == 1, "exactly one hold must succeed"
    assert all(isinstance(e, Conflict) for e in errors), "the loser must get Conflict"

    inv = (await db.execute(select(UnitInventory).where(UnitInventory.asset_id == asset.id))).scalar_one()
    assert inv.state == "HELD"
    holds = (await db.execute(select(func.count()).select_from(InventoryHold))).scalar_one()
    assert holds == 1


async def test_double_reserve_invariant(db, tenant, owner_ctx):
    """§102: one unit cannot have two active reservations — mark_reserved from
    a non-AVAILABLE/HELD state is impossible."""
    from app.properties.service import mark_reserved

    asset = await _make_unit(db, tenant)
    async with db.begin():
        await mark_reserved(db, tenant_id=tenant.id, asset_id=asset.id,
                            reservation_id=uuid.uuid4())
        with pytest.raises(Conflict):
            await mark_reserved(db, tenant_id=tenant.id, asset_id=asset.id,
                                reservation_id=uuid.uuid4())


import uuid  # noqa: E402  (used in tests above)


async def test_availability_check_reports_expired_hold_as_stale(db, tenant, owner_ctx):
    from datetime import UTC, datetime, timedelta

    asset = await _make_unit(db, tenant)
    async with db.begin():
        hold = await hold_unit(db, tenant_id=tenant.id, asset_id=asset.id)
        # force expiry
        row = (
            await db.execute(select(InventoryHold).where(InventoryHold.id == hold.id))
        ).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)

    async with db.begin():
        avail = await check_availability(db, tenant_id=tenant.id, asset_ids=[asset.id])
    # V4 hardening: expiry now RECONCILES state (read truth == write truth)
    assert avail[str(asset.id)]["state"] == "AVAILABLE"
    inv = (
        await db.execute(select(UnitInventory).where(UnitInventory.asset_id == asset.id))
    ).scalar_one()
    assert inv.state == "AVAILABLE" and inv.hold_id is None


async def test_listing_created_with_price_snapshot(db, tenant, owner_ctx):
    asset = await _make_unit(db, tenant)
    async with db.begin():
        await set_price(db, tenant_id=tenant.id, asset_id=asset.id, amount="4000000")
    async with db.begin():
        listing = await create_listing(
            db, tenant_id=tenant.id, asset_id=asset.id,
            title={"ar": "شقة للبيع في التجمع", "en": "Apartment for sale in New Cairo"},
            description={"ar": "شقة تشطيب سوبر لوكس"},
        )
    assert listing.asking_price_amount == Decimal("4000000.0000")
    assert listing.price_version_id is not None
    async with db.begin():
        await transition_listing(db, listing=listing, event="activate")
    assert listing.status == "active"


async def test_payment_plan_and_mandate(db, tenant, owner_ctx):
    asset = await _make_unit(db, tenant)
    async with db.begin():
        plan = await create_payment_plan(
            db, tenant_id=tenant.id, asset_id=asset.id, name="خطة 5 سنين",
            down_payment_pct=Decimal("10"), installment_count=20,
            installment_period_months=3, total=Decimal("4400000"),
        )
        mandate = await create_mandate(
            db, tenant_id=tenant.id, asset_id=asset.id, mandate_type="exclusive",
            rights={"listing": True, "referral": True},
            commission_terms={"agency_pct": 2.5},
        )
    assert plan.installment_count == 20
    assert mandate.mandate_type == "exclusive"
