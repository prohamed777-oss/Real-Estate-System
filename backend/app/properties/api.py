"""Properties/supply/inventory/pricing API."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFound
from app.core.pagination import CursorPage, encode_cursor
from app.core.permissions import (
    INVENTORY_READ,
    INVENTORY_WRITE,
    PROPERTIES_READ,
    PROPERTIES_WRITE,
    require,
)
from app.core.tenancy import AuthContext
from app.properties.models import (
    PriceVersion,
    PropertyAsset,
    UnitInventory,
)
from app.properties.service import (
    check_availability,
    create_asset,
    create_building,
    create_developer,
    create_mandate,
    create_payment_plan,
    create_project,
    get_asset,
    get_current_price,
    hold_unit,
    release_unit,
    set_price,
)

router = APIRouter(tags=["properties"])


class DeveloperIn(BaseModel):
    name: str
    contact: dict[str, Any] = {}


class ProjectIn(BaseModel):
    name: str
    developer_id: uuid.UUID | None = None
    location: dict[str, Any] = {}
    description: dict[str, Any] = {}
    delivery_date: str | None = None


class BuildingIn(BaseModel):
    project_id: uuid.UUID
    name: str
    floors: int | None = None


class AssetIn(BaseModel):
    title: str
    property_type: str
    asset_type: str = "standalone"
    project_id: uuid.UUID | None = None
    building_id: uuid.UUID | None = None
    owner_person_id: uuid.UUID | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    area_value: Decimal | None = None
    finishing: str | None = None
    floor: str | None = None
    view: str | None = None
    delivery_status: str | None = None
    location: dict[str, Any] = {}
    attributes: dict[str, Any] = {}
    media: list = []
    purpose: str = "sale"


class PriceIn(BaseModel):
    amount: Decimal
    currency: str = "EGP"
    source: str | None = None


class MandateIn(BaseModel):
    asset_id: uuid.UUID
    owner_person_id: uuid.UUID | None = None
    mandate_type: str = "open"
    rights: dict[str, Any] = {}
    commission_terms: dict[str, Any] = {}
    expires_at: str | None = None


class PaymentPlanIn(BaseModel):
    name: str
    asset_id: uuid.UUID | None = None
    down_payment_amount: Decimal | None = None
    down_payment_pct: Decimal | None = None
    installment_amount: Decimal | None = None
    installment_count: int | None = None
    installment_period_months: int | None = None
    maintenance_fee: Decimal | None = None
    admin_fee: Decimal | None = None
    total: Decimal | None = None
    currency: str = "EGP"
    is_default: bool = False


@router.post("/developers", status_code=201)
async def post_developer(
    body: DeveloperIn, auth: AuthContext = Depends(require(PROPERTIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    dev = await create_developer(session, tenant_id=auth.tenant_id, name=body.name,
                                 contact=body.contact, actor_id=auth.user_id)
    return {"id": str(dev.id), "name": dev.name}


@router.post("/projects", status_code=201)
async def post_project(
    body: ProjectIn, auth: AuthContext = Depends(require(PROPERTIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await create_project(
        session, tenant_id=auth.tenant_id, name=body.name, developer_id=body.developer_id,
        location=body.location, description=body.description,
        delivery_date=body.delivery_date, actor_id=auth.user_id,
    )
    return {"id": str(project.id), "name": project.name}


@router.post("/buildings", status_code=201)
async def post_building(
    body: BuildingIn, auth: AuthContext = Depends(require(PROPERTIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    building = await create_building(session, tenant_id=auth.tenant_id,
                                     project_id=body.project_id, name=body.name, floors=body.floors)
    return {"id": str(building.id), "name": building.name}


@router.post("/assets", status_code=201)
async def post_asset(
    body: AssetIn, auth: AuthContext = Depends(require(PROPERTIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    asset = await create_asset(
        session, tenant_id=auth.tenant_id, **body.model_dump(), actor_id=auth.user_id,
    )
    return {"id": str(asset.id), "title": asset.title, "asset_type": asset.asset_type}


@router.get("/assets")
async def list_assets(
    auth: AuthContext = Depends(require(PROPERTIES_READ)),
    session: AsyncSession = Depends(get_session),
    asset_type: str | None = None,
    property_type: str | None = None,
    project_id: uuid.UUID | None = None,
    city: str | None = None,
    min_bedrooms: int | None = None,
    q: str | None = None,
    limit: int = Query(default=50, le=200),
    cursor: str | None = None,
) -> CursorPage:
    query = (
        select(PropertyAsset)
        .where(PropertyAsset.tenant_id == auth.tenant_id, PropertyAsset.is_active.is_(True))
        .order_by(PropertyAsset.created_at.desc(), PropertyAsset.id.desc())
    )
    if asset_type:
        query = query.where(PropertyAsset.asset_type == asset_type)
    if property_type:
        query = query.where(PropertyAsset.property_type == property_type)
    if project_id:
        query = query.where(PropertyAsset.project_id == project_id)
    if min_bedrooms is not None:
        query = query.where(PropertyAsset.bedrooms >= min_bedrooms)
    if city:
        query = query.where(PropertyAsset.location["city"].astext.ilike(f"%{city}%"))
    if q:
        # Full-text search (§24): ranked lexical search over the generated tsvector
        sv = PropertyAsset.__table__.c.search_vector
        tsq = func.websearch_to_tsquery("simple", q)
        query = query.where(sv.op("@@")(tsq)).order_by(
            func.ts_rank(sv, tsq).desc(),
            PropertyAsset.created_at.desc(),
            PropertyAsset.id.desc(),
        )
    rows = (await session.execute(query.limit(limit))).scalars().all()
    items = [
        {
            "id": str(a.id), "title": a.title, "asset_type": a.asset_type,
            "property_type": a.property_type, "bedrooms": a.bedrooms, "bathrooms": a.bathrooms,
            "area_value": str(a.area_value) if a.area_value else None,
            "location": a.location, "finishing": a.finishing,
            "delivery_status": a.delivery_status,
            "project_id": str(a.project_id) if a.project_id else None,
            "media": a.media,
        }
        for a in rows
    ]
    next_cursor = (
        encode_cursor(created_at=rows[-1].created_at, id_=rows[-1].id) if len(items) == limit and rows else None
    )
    return CursorPage(items=items, next_cursor=next_cursor, has_more=bool(next_cursor))


@router.get("/assets/{asset_id}")
async def get_asset_detail(
    asset_id: uuid.UUID,
    auth: AuthContext = Depends(require(PROPERTIES_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    asset = await get_asset(session, auth.tenant_id, asset_id)
    inv = (
        await session.execute(
            select(UnitInventory).where(UnitInventory.asset_id == asset_id)
        )
    ).scalar_one_or_none()
    price = await get_current_price(session, auth.tenant_id, asset_id)
    return {
        "id": str(asset.id), "title": asset.title, "asset_type": asset.asset_type,
        "property_type": asset.property_type, "purpose": asset.purpose,
        "bedrooms": asset.bedrooms, "bathrooms": asset.bathrooms,
        "area_value": str(asset.area_value) if asset.area_value else None,
        "area_unit": asset.area_unit, "finishing": asset.finishing, "floor": asset.floor,
        "view": asset.view, "delivery_status": asset.delivery_status,
        "location": asset.location, "attributes": asset.attributes, "media": asset.media,
        "provenance": {
            "source_type": asset.source_type, "external_id": asset.external_id,
            "last_synced_at": asset.last_synced_at.isoformat() if asset.last_synced_at else None,
            "sync_status": asset.sync_status,
            "verified_at": asset.verified_at.isoformat() if asset.verified_at else None,
            "confidence": asset.confidence,
        },
        "inventory": {"state": inv.state, "version": inv.version} if inv else None,
        "price": {"amount": str(price.amount), "currency": price.currency,
                  "valid_from": price.valid_from.isoformat()} if price else None,
    }


@router.post("/assets/{asset_id}/price")
async def post_price(
    asset_id: uuid.UUID,
    body: PriceIn,
    auth: AuthContext = Depends(require(PROPERTIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    version = await set_price(
        session, tenant_id=auth.tenant_id, asset_id=asset_id, amount=body.amount,
        currency=body.currency, source=body.source, actor_id=auth.user_id,
    )
    return {"price_version_id": str(version.id), "amount": str(version.amount),
            "currency": version.currency, "valid_from": version.valid_from.isoformat()}


@router.get("/assets/{asset_id}/price-history")
async def price_history(
    asset_id: uuid.UUID,
    auth: AuthContext = Depends(require(PROPERTIES_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(PriceVersion)
            .where(PriceVersion.tenant_id == auth.tenant_id, PriceVersion.asset_id == asset_id)
            .order_by(PriceVersion.valid_from.desc())
        )
    ).scalars().all()
    return [
        {"id": str(p.id), "amount": str(p.amount), "currency": p.currency,
         "valid_from": p.valid_from.isoformat(),
         "valid_to": p.valid_to.isoformat() if p.valid_to else None, "source": p.source}
        for p in rows
    ]


@router.post("/mandates", status_code=201)
async def post_mandate(
    body: MandateIn,
    auth: AuthContext = Depends(require(PROPERTIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from datetime import datetime

    mandate = await create_mandate(
        session, tenant_id=auth.tenant_id, asset_id=body.asset_id,
        owner_person_id=body.owner_person_id, mandate_type=body.mandate_type,
        rights=body.rights, commission_terms=body.commission_terms,
        expires_at=datetime.fromisoformat(body.expires_at) if body.expires_at else None,
        actor_id=auth.user_id,
    )
    return {"id": str(mandate.id), "mandate_type": mandate.mandate_type}


@router.post("/payment-plans", status_code=201)
async def post_payment_plan(
    body: PaymentPlanIn,
    auth: AuthContext = Depends(require(PROPERTIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    plan = await create_payment_plan(session, tenant_id=auth.tenant_id, **body.model_dump())
    return {"id": str(plan.id), "name": plan.name}


# ---------- inventory ----------
@router.post("/inventory/{asset_id}/hold")
async def post_hold(
    asset_id: uuid.UUID,
    auth: AuthContext = Depends(require(INVENTORY_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:

    hold = await hold_unit(
        session, tenant_id=auth.tenant_id, asset_id=asset_id,
        created_by=str(auth.user_id), reason="manual hold via API",
        ttl_minutes=30,
    )
    return {"hold_id": str(hold.id), "expires_at": hold.expires_at.isoformat()}


@router.post("/inventory/{asset_id}/release")
async def post_release(
    asset_id: uuid.UUID,
    auth: AuthContext = Depends(require(INVENTORY_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    inv = await release_unit(session, tenant_id=auth.tenant_id, asset_id=asset_id,
                             actor_id=auth.user_id)
    return {"asset_id": str(asset_id), "state": inv.state}


@router.get("/inventory/availability")
async def availability(
    auth: AuthContext = Depends(require(INVENTORY_READ)),
    session: AsyncSession = Depends(get_session),
    asset_ids: str = Query(...),
) -> dict[str, Any]:
    ids = [uuid.UUID(x) for x in asset_ids.split(",") if x.strip()]
    if not ids:
        raise NotFound("No asset ids provided")
    return await check_availability(session, tenant_id=auth.tenant_id, asset_ids=ids)
