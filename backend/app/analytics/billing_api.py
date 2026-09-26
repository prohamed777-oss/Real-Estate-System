"""Billing API (§99): usage summary, subscription, quota preview."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.billing import usage_summary
from app.core.db import get_session
from app.core.errors import NotFound
from app.core.permissions import SETTINGS_READ, SETTINGS_WRITE, require
from app.core.tenancy import AuthContext
from sqlalchemy import select

from app.analytics.models import Subscription

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/usage")
async def get_usage(
    auth: AuthContext = Depends(require(SETTINGS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await usage_summary(session, tenant_id=auth.tenant_id)


@router.get("/subscription")
async def get_subscription(
    auth: AuthContext = Depends(require(SETTINGS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    sub = (
        await session.execute(
            select(Subscription).where(Subscription.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        raise NotFound("No subscription")
    return {"plan": sub.plan, "status": sub.status, "seats": sub.seats}


@router.post("/subscription/{plan}")
async def set_plan(
    plan: str,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.analytics.billing import PLAN_LIMITS

    if plan not in PLAN_LIMITS:
        raise NotFound(f"Unknown plan: {plan}")
    sub = (
        await session.execute(
            select(Subscription).where(Subscription.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        from app.analytics.billing import get_subscription

        sub = await get_subscription(session, auth.tenant_id)
    sub.plan = plan
    return {"plan": sub.plan, "limits": PLAN_LIMITS[plan]}
