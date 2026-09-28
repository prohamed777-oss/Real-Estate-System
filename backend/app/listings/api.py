"""Listings API (§21-22)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFound
from app.core.permissions import LISTINGS_READ, LISTINGS_WRITE, require
from app.core.tenancy import AuthContext
from app.listings.models import Listing, ListingChannel
from app.properties.service import create_listing, transition_listing

router = APIRouter(prefix="/listings", tags=["listings"])


class ListingIn(BaseModel):
    asset_id: uuid.UUID
    title: dict[str, str]  # {"ar": ..., "en": ...}
    description: dict[str, str] = {}


class ListingTransitionIn(BaseModel):
    event: str  # activate | pause | expire | archive


@router.post("", status_code=201)
async def create(
    body: ListingIn,
    auth: AuthContext = Depends(require(LISTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    listing = await create_listing(
        session, tenant_id=auth.tenant_id, asset_id=body.asset_id, title=body.title,
        description=body.description, created_by=str(auth.user_id), actor_id=auth.user_id,
    )
    return {"id": str(listing.id), "status": listing.status,
            "price": str(listing.asking_price_amount) if listing.asking_price_amount else None}


@router.get("")
async def list_listings(
    auth: AuthContext = Depends(require(LISTINGS_READ)),
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
    limit: int = Query(default=50, le=200),
) -> list[dict[str, Any]]:
    query = select(Listing).where(Listing.tenant_id == auth.tenant_id)
    if status:
        query = query.where(Listing.status == status)
    rows = (await session.execute(query.order_by(Listing.created_at.desc()).limit(limit))).scalars().all()
    return [
        {
            "id": str(listing.id), "asset_id": str(listing.asset_id), "title": listing.title,
            "description": listing.description,
            "asking_price": str(listing.asking_price_amount) if listing.asking_price_amount else None,
            "asking_price_currency": listing.asking_price_currency, "status": listing.status,
        }
        for listing in rows
    ]


@router.get("/{listing_id}")
async def get_listing(
    listing_id: uuid.UUID,
    auth: AuthContext = Depends(require(LISTINGS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    listing = (
        await session.execute(
            select(Listing).where(Listing.id == listing_id, Listing.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if listing is None:
        raise NotFound("Listing not found")
    return {
        "id": str(listing.id), "asset_id": str(listing.asset_id), "title": listing.title,
        "description": listing.description,
        "asking_price": str(listing.asking_price_amount) if listing.asking_price_amount else None,
        "status": listing.status, "media": listing.media,
    }


@router.post("/{listing_id}/transition")
async def do_transition(
    listing_id: uuid.UUID,
    body: ListingTransitionIn,
    auth: AuthContext = Depends(require(LISTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    listing = (
        await session.execute(
            select(Listing).where(Listing.id == listing_id, Listing.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if listing is None:
        raise NotFound("Listing not found")
    await transition_listing(session, listing=listing, event=body.event, actor_id=auth.user_id)
    return {"id": str(listing.id), "status": listing.status}


@router.post("/{listing_id}/channels", status_code=201)
async def add_channel(
    listing_id: uuid.UUID,
    body: dict[str, str],
    auth: AuthContext = Depends(require(LISTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    channel = ListingChannel(
        tenant_id=auth.tenant_id, listing_id=listing_id,
        channel=body.get("channel", "website"), status="draft",
        payload=body.get("payload", {}),
    )
    session.add(channel)
    await session.flush()
    return {"id": str(channel.id), "channel": channel.channel, "status": channel.status}
