"""Property/supply/inventory/pricing services (§13-20).

The reservation path (§18) is the most protected code in the system:
    request → validate → authorization → check state → FOR UPDATE lock
    → create hold/reservation → update inventory → commit → event
Two concurrent reservations of the same unit: exactly one succeeds.
No LLM ever decides availability (§1.2).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.decision.models import InventoryLedger
from app.events.outbox import emit
from app.listings.models import Listing
from app.properties.models import (
    Building,
    Developer,
    InventoryHold,
    Mandate,
    PaymentPlan,
    PriceVersion,
    Project,
    PropertyAsset,
    UnitInventory,
)
from app.properties.statemachine import INVENTORY_EVENTS, InventoryStateMachine, ListingStateMachine

HOLD_TTL_MINUTES = 30


# ---------- catalog ----------
async def create_developer(session: AsyncSession, *, tenant_id: uuid.UUID, name: str,
                           contact: dict | None = None, actor_id=None) -> Developer:
    dev = Developer(tenant_id=tenant_id, name=name, contact=contact or {})
    session.add(dev)
    await session.flush()
    await audit(session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
                action="developer.created", entity_type="developer", entity_id=dev.id,
                after={"name": name})
    return dev


async def create_project(session: AsyncSession, *, tenant_id: uuid.UUID, name: str,
                         developer_id: uuid.UUID | None = None,
                         location: dict | None = None, description: dict | None = None,
                         delivery_date: str | None = None, actor_id=None) -> Project:
    project = Project(
        tenant_id=tenant_id, developer_id=developer_id, name=name,
        location=location or {}, description=description or {},
        delivery_date=delivery_date,
        location_timezone=(location or {}).get("timezone", "Africa/Cairo"),
    )
    session.add(project)
    await session.flush()
    await audit(session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
                action="project.created", entity_type="project", entity_id=project.id,
                after={"name": name})
    return project


async def create_building(session: AsyncSession, *, tenant_id: uuid.UUID, project_id: uuid.UUID,
                          name: str, floors: int | None = None) -> Building:
    building = Building(tenant_id=tenant_id, project_id=project_id, name=name, floors=floors)
    session.add(building)
    await session.flush()
    return building


async def create_asset(
    session: AsyncSession, *, tenant_id: uuid.UUID, title: str, property_type: str,
    asset_type: str = "standalone", project_id: uuid.UUID | None = None,
    building_id: uuid.UUID | None = None, owner_person_id: uuid.UUID | None = None,
    bedrooms: int | None = None, bathrooms: int | None = None, area_value: Decimal | None = None,
    finishing: str | None = None, floor: str | None = None, view: str | None = None,
    delivery_status: str | None = None, location: dict | None = None,
    attributes: dict | None = None, media: list | None = None, purpose: str = "sale",
    source_type: str | None = None, external_id: str | None = None, actor_id=None,
) -> PropertyAsset:
    if asset_type == "unit" and project_id is None:
        raise ValidationFailed("Unit assets must belong to a project")
    asset = PropertyAsset(
        tenant_id=tenant_id, asset_type=asset_type, project_id=project_id,
        building_id=building_id, owner_person_id=owner_person_id, title=title,
        property_type=property_type, purpose=purpose, bedrooms=bedrooms, bathrooms=bathrooms,
        area_value=area_value, finishing=finishing, floor=floor, view=view,
        delivery_status=delivery_status, location=location or {},
        attributes=attributes or {}, media=media or [],
        source_type=source_type or "owner", external_id=external_id,
        verified_at=datetime.now(UTC), confidence=90,
    )
    session.add(asset)
    await session.flush()

    # Unit assets get an inventory row immediately (AVAILABLE by default)
    if asset_type == "unit":
        session.add(UnitInventory(tenant_id=tenant_id, asset_id=asset.id, state="AVAILABLE"))
        await emit(
            session, event_name="property.created", tenant_id=tenant_id,
            aggregate_type="property_asset", aggregate_id=asset.id,
            payload={"asset_id": str(asset.id), "asset_type": asset_type,
                     "property_type": property_type, "title": title},
        )
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="property.created", entity_type="property_asset", entity_id=asset.id,
        after={"title": title, "property_type": property_type},
    )
    return asset


async def get_asset(session: AsyncSession, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> PropertyAsset:
    asset = (
        await session.execute(
            select(PropertyAsset).where(
                PropertyAsset.id == asset_id, PropertyAsset.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise NotFound("Asset not found")
    return asset


# ---------- pricing (§20: append-only versions) ----------
async def set_price(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID,
    amount: Decimal | str, currency: str = "EGP", source: str | None = None,
    actor_id=None, provenance: dict | None = None,
) -> PriceVersion:
    await get_asset(session, tenant_id, asset_id)
    now = datetime.now(UTC)
    # serialize concurrent re-pricings: lock the asset's price rows first
    await session.execute(
        select(PriceVersion.id)
        .where(PriceVersion.tenant_id == tenant_id, PriceVersion.asset_id == asset_id)
        .order_by(PriceVersion.valid_from.desc())
        .limit(1)
        .with_for_update()
    )
    # close the current version, open the new one — history never rewritten
    current = (
        await session.execute(
            select(PriceVersion).where(
                PriceVersion.tenant_id == tenant_id,
                PriceVersion.asset_id == asset_id,
                PriceVersion.valid_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if current is not None:
        if current.amount == Decimal(str(amount)) and current.currency == currency:
            return current
        current.valid_to = now
    version = PriceVersion(
        tenant_id=tenant_id, asset_id=asset_id, amount=Decimal(str(amount)),
        currency=currency, valid_from=now, source=source, created_by=str(actor_id) if actor_id else None,
        provenance=provenance or {"verified_at": now.isoformat(), "confidence": 90},
    )
    session.add(version)
    await session.flush()
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="price.changed", entity_type="property_asset", entity_id=asset_id,
        before={"amount": str(current.amount) if current else None, "currency": current.currency if current else None},
        after={"amount": str(version.amount), "currency": currency}, source=source or "api",
    )
    await emit(
        session, event_name="property.price_changed", tenant_id=tenant_id,
        aggregate_type="property_asset", aggregate_id=asset_id,
        payload={"asset_id": str(asset_id), "amount": str(version.amount), "currency": currency},
    )
    return version


async def get_current_price(session: AsyncSession, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> PriceVersion | None:
    return (
        await session.execute(
            select(PriceVersion).where(
                PriceVersion.tenant_id == tenant_id,
                PriceVersion.asset_id == asset_id,
                PriceVersion.valid_to.is_(None),
            )
        )
    ).scalar_one_or_none()


async def create_payment_plan(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str, asset_id: uuid.UUID | None = None,
    down_payment_amount: Decimal | None = None, down_payment_pct: Decimal | None = None,
    installment_amount: Decimal | None = None, installment_count: int | None = None,
    installment_period_months: int | None = None, maintenance_fee: Decimal | None = None,
    admin_fee: Decimal | None = None, total: Decimal | None = None, currency: str = "EGP",
    is_default: bool = False,
) -> PaymentPlan:
    plan = PaymentPlan(
        tenant_id=tenant_id, asset_id=asset_id, name=name, currency=currency,
        down_payment_amount=down_payment_amount, down_payment_pct=down_payment_pct,
        installment_amount=installment_amount, installment_count=installment_count,
        installment_period_months=installment_period_months, maintenance_fee=maintenance_fee,
        admin_fee=admin_fee, total=total, is_default=is_default,
    )
    session.add(plan)
    await session.flush()
    return plan


# ---------- inventory (§17-18): the transactional core ----------

async def _ledger(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID,
    from_state: str | None, to_state: str, actor_type: str = "user",
    actor_id=None, reason: str | None = None, tx_id: str | None = None,
) -> None:
    """Append-only inventory ledger (V4 3.4) — written inside the same tx."""
    session.add(InventoryLedger(
        tenant_id=tenant_id, asset_id=asset_id, from_state=from_state,
        to_state=to_state, actor_type=actor_type,
        actor_id=str(actor_id) if actor_id else None, reason=reason, tx_id=tx_id,
    ))
    await session.flush()

async def _lock_inventory(session: AsyncSession, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> UnitInventory:
    """SELECT ... FOR UPDATE — serializes concurrent inventory mutations."""
    inv = (
        await session.execute(
            select(UnitInventory)
            .where(UnitInventory.tenant_id == tenant_id, UnitInventory.asset_id == asset_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if inv is None:
        raise NotFound("Inventory row not found for this unit")
    return inv


async def hold_unit(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID,
    created_by: str | None = None, reason: str | None = None, ttl_minutes: int = HOLD_TTL_MINUTES,
) -> InventoryHold:
    async def _do() -> InventoryHold:
        inv = await _lock_inventory(session, tenant_id, asset_id)
        sm = InventoryStateMachine(inv.state)
        sm.fire("hold")
        now = datetime.now(UTC)
        hold = InventoryHold(
            tenant_id=tenant_id, asset_id=asset_id, created_by=created_by, reason=reason,
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
        session.add(hold)
        await session.flush()
        from_state = inv.state
        inv.state = "HELD"
        inv.hold_id = hold.id
        inv.version += 1
        await _ledger(session, tenant_id=tenant_id, asset_id=asset_id,
                      from_state=from_state, to_state="HELD",
                      actor_type="user", actor_id=created_by, reason=reason)
        await audit(
            session, tenant_id=tenant_id, actor_type="user", actor_id=created_by,
            action="inventory.changed", entity_type="property_asset", entity_id=asset_id,
            before={"state": "AVAILABLE"}, after={"state": "HELD", "hold_id": str(hold.id)},
        )
        await emit(
            session, event_name=INVENTORY_EVENTS["hold"], tenant_id=tenant_id,
            aggregate_type="property_asset", aggregate_id=asset_id,
            payload={"asset_id": str(asset_id), "hold_id": str(hold.id),
                     "expires_at": hold.expires_at.isoformat()},
        )
        return hold

    return await _do()


async def release_unit(session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID,
                       actor_id=None, reason: str | None = None) -> UnitInventory:
    inv = await _lock_inventory(session, tenant_id, asset_id)
    sm = InventoryStateMachine(inv.state)
    target_event = "release" if sm.can("release") else "cancel" if sm.can("cancel") else None
    if target_event is None:
        raise Conflict(f"Cannot release unit in state {inv.state}")
    before = inv.state
    sm.fire(target_event)
    inv.state = sm.state
    if inv.state == "AVAILABLE" and inv.hold_id:
        hold = await session.get(InventoryHold, inv.hold_id)
        if hold and hold.status == "active":
            hold.status = "released"
            hold.released_at = datetime.now(UTC)
        inv.hold_id = None
        inv.reservation_id = None
    inv.version += 1
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="inventory.changed", entity_type="property_asset", entity_id=asset_id,
        before={"state": before}, after={"state": inv.state, "reason": reason},
    )
    await _ledger(session, tenant_id=tenant_id, asset_id=asset_id,
                  from_state=before, to_state=inv.state, actor_id=actor_id, reason=reason)
    await emit(
        session, event_name=INVENTORY_EVENTS["release"], tenant_id=tenant_id,
        aggregate_type="property_asset", aggregate_id=asset_id,
        payload={"asset_id": str(asset_id), "from": before, "to": inv.state},
    )
    return inv


async def mark_reserved(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID,
    reservation_id: uuid.UUID, actor_type: str = "user", actor_id=None,
) -> UnitInventory:
    """RESERVED transition — called by the ReservationService (M5), never directly by AI."""
    inv = await _lock_inventory(session, tenant_id, asset_id)
    sm = InventoryStateMachine(inv.state)
    if not sm.can("reserve"):
        raise Conflict(
            f"Unit not reservable in state {inv.state}",
            details={"state": inv.state},
        )
    before = inv.state
    sm.fire("reserve")
    inv.state = "RESERVED"
    inv.reservation_id = reservation_id
    inv.version += 1
    await audit(
        session, tenant_id=tenant_id, actor_type=actor_type, actor_id=actor_id,
        action="inventory.changed", entity_type="property_asset", entity_id=asset_id,
        before={"state": before}, after={"state": "RESERVED", "reservation_id": str(reservation_id)},
    )
    await _ledger(session, tenant_id=tenant_id, asset_id=asset_id,
                  from_state=before, to_state="RESERVED", actor_type=actor_type, actor_id=actor_id)
    await emit(
        session, event_name=INVENTORY_EVENTS["reserve"], tenant_id=tenant_id,
        aggregate_type="property_asset", aggregate_id=asset_id,
        payload={"asset_id": str(asset_id), "reservation_id": str(reservation_id)},
    )
    return inv


async def mark_contracted(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID,
    contract_id: uuid.UUID | None = None, actor_id=None,
) -> UnitInventory:
    """RESERVED → CONTRACTED (§17) — called by the contract service."""
    inv = await _lock_inventory(session, tenant_id, asset_id)
    sm = InventoryStateMachine(inv.state)
    if not sm.can("contract"):
        raise Conflict(f"Cannot contract unit in state {inv.state}",
                       details={"state": inv.state})
    before = inv.state
    sm.fire("contract")
    inv.state = sm.state
    inv.version += 1
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="inventory.changed", entity_type="property_asset", entity_id=asset_id,
        before={"state": before}, after={"state": "CONTRACTED",
                                          "contract_id": str(contract_id) if contract_id else None},
    )
    await _ledger(session, tenant_id=tenant_id, asset_id=asset_id,
                  from_state=before, to_state="CONTRACTED", actor_id=actor_id)
    await emit(
        session, event_name=INVENTORY_EVENTS["contract"], tenant_id=tenant_id,
        aggregate_type="property_asset", aggregate_id=asset_id,
        payload={"asset_id": str(asset_id), "from": before, "to": "CONTRACTED"},
    )
    return inv


async def mark_sold(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID, actor_id=None,
) -> UnitInventory:
    """CONTRACTED → SOLD (§17)."""
    inv = await _lock_inventory(session, tenant_id, asset_id)
    sm = InventoryStateMachine(inv.state)
    if not sm.can("complete"):
        raise Conflict(f"Cannot complete unit in state {inv.state}",
                       details={"state": inv.state})
    before = inv.state
    sm.fire("complete")
    inv.state = sm.state
    inv.version += 1
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="inventory.changed", entity_type="property_asset", entity_id=asset_id,
        before={"state": before}, after={"state": "SOLD"},
    )
    await _ledger(session, tenant_id=tenant_id, asset_id=asset_id,
                  from_state=before, to_state="SOLD", actor_id=actor_id)
    await emit(
        session, event_name=INVENTORY_EVENTS["complete"], tenant_id=tenant_id,
        aggregate_type="property_asset", aggregate_id=asset_id,
        payload={"asset_id": str(asset_id), "from": before, "to": "SOLD"},
    )
    return inv


async def check_availability(session: AsyncSession, *, tenant_id: uuid.UUID,
                             asset_ids: list[uuid.UUID]) -> dict[str, dict]:
    """Read-tool availability check. Honors hold expiry (§104 freshness)."""
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(UnitInventory).where(
                UnitInventory.tenant_id == tenant_id, UnitInventory.asset_id.in_(asset_ids)
            )
        )
    ).scalars().all()
    out: dict[str, dict] = {}
    for inv in rows:
        if inv.state == "HELD" and inv.hold_id:
            hold = await session.get(InventoryHold, inv.hold_id)
            if hold and hold.status == "active" and hold.expires_at < now:
                # read truth == write truth: expire the hold transactionally
                # (V4 audit fix #14) instead of reporting a phantom state
                hold.status = "expired"
                inv.state = "AVAILABLE"
                inv.hold_id = None
                inv.version += 1
                await _ledger(session, tenant_id=tenant_id, asset_id=inv.asset_id,
                              from_state="HELD", to_state="AVAILABLE",
                              actor_type="system", actor_id="hold-expiry",
                              reason="hold expired")
                await emit(
                    session, event_name=INVENTORY_EVENTS["release"],
                    tenant_id=tenant_id, aggregate_type="property_asset",
                    aggregate_id=inv.asset_id,
                    payload={"asset_id": str(inv.asset_id), "reason": "hold_expired"},
                )
        out[str(inv.asset_id)] = {"state": inv.state, "confidence": inv.availability_confidence}
    return out


async def create_mandate(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID,
    owner_person_id: uuid.UUID | None = None, mandate_type: str = "open",
    rights: dict | None = None, commission_terms: dict | None = None,
    start_at: datetime | None = None, expires_at: datetime | None = None, actor_id=None,
) -> Mandate:
    mandate = Mandate(
        tenant_id=tenant_id, asset_id=asset_id, owner_person_id=owner_person_id,
        mandate_type=mandate_type, rights=rights or {}, commission_terms=commission_terms or {},
        start_at=start_at or datetime.now(UTC), expires_at=expires_at,
    )
    session.add(mandate)
    await session.flush()
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="mandate.created", entity_type="mandate", entity_id=mandate.id,
        after={"asset_id": str(asset_id), "type": mandate_type},
    )
    return mandate


# ---------- listings (§21-22) ----------
async def create_listing(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID,
    title: dict[str, str], description: dict[str, str] | None = None,
    created_by: str | None = None, actor_id=None,
) -> Listing:
    await get_asset(session, tenant_id, asset_id)
    price = await get_current_price(session, tenant_id, asset_id)
    listing = Listing(
        tenant_id=tenant_id, asset_id=asset_id, title=title,
        description=description or {},
        asking_price_amount=price.amount if price else None,
        asking_price_currency=price.currency if price else "EGP",
        price_version_id=price.id if price else None,
        created_by=created_by,
    )
    session.add(listing)
    await session.flush()
    return listing


async def transition_listing(
    session: AsyncSession, *, listing: Listing, event: str, actor_id=None,
) -> Listing:
    before = listing.status
    sm = ListingStateMachine(listing.status)
    sm.fire(event)
    listing.status = sm.state
    await emit(
        session, event_name=f"listing.{event}", tenant_id=listing.tenant_id,
        aggregate_type="listing", aggregate_id=listing.id,
        payload={"listing_id": str(listing.id), "from": before, "to": listing.status},
    )
    return listing
