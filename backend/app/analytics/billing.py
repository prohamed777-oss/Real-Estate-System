"""Billing & usage enforcement (§99): quotas from plan limits, metered usage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.models import Subscription, UsageMeter
from app.core.errors import DomainError

PLAN_LIMITS: dict[str, dict[str, Any]] = {
    "trial": {"ai_requests_month": 500, "messages_month": 2000, "seats": 5, "storage_mb": 1024},
    "starter": {"ai_requests_month": 5000, "messages_month": 20000, "seats": 15, "storage_mb": 10240},
    "pro": {"ai_requests_month": 50000, "messages_month": 200000, "seats": 100, "storage_mb": 102400},
    "enterprise": {"ai_requests_month": 10**9, "messages_month": 10**9, "seats": 10**6, "storage_mb": 10**9},
}


class QuotaExceeded(DomainError):
    status_code = 429
    code = "quota_exceeded"


async def record_usage(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: str,
    quantity: int = 1, cost_usd: float | None = None, metadata: dict[str, Any] | None = None,
) -> None:
    session.add(UsageMeter(
        tenant_id=tenant_id, kind=kind, quantity=quantity,
        cost_usd=cost_usd, metadata_=metadata or {},
    ))
    await session.flush()


async def get_subscription(session: AsyncSession, tenant_id: uuid.UUID) -> Subscription:
    sub = (
        await session.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        sub = Subscription(tenant_id=tenant_id, plan="trial", status="active",
                            period_start=datetime.now(UTC))
        session.add(sub)
        await session.flush()
    return sub


async def check_quota(session: AsyncSession, *, tenant_id: uuid.UUID, kind: str) -> dict[str, Any]:
    """Raise QuotaExceeded when the tenant's plan limit for `kind` is exhausted."""
    limits = PLAN_LIMITS.get("trial", {})
    sub = await get_subscription(session, tenant_id)
    limits = PLAN_LIMITS.get(sub.plan, limits)
    limit_key = f"{kind}_month"
    limit = limits.get(limit_key)
    if limit is None:
        return {"allowed": True}
    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = (
        await session.execute(
            select(func.coalesce(func.sum(UsageMeter.quantity), 0)).where(
                UsageMeter.tenant_id == tenant_id,
                UsageMeter.kind == kind,
                UsageMeter.occurred_at >= month_start,
            )
        )
    ).scalar_one()
    used = int(used or 0)
    if used >= int(limit):
        raise QuotaExceeded(
            f"Monthly {kind} quota exhausted for plan {sub.plan}",
            details={"kind": kind, "used": used, "limit": int(limit), "plan": sub.plan},
        )
    return {"allowed": True, "used": used, "limit": int(limit), "plan": sub.plan}


async def usage_summary(session: AsyncSession, *, tenant_id: uuid.UUID) -> dict[str, Any]:
    sub = await get_subscription(session, tenant_id)
    limits = PLAN_LIMITS.get(sub.plan, PLAN_LIMITS["trial"])
    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out: dict[str, Any] = {"plan": sub.plan, "seats_limit": limits.get("seats")}
    for kind in ("ai_requests", "messages", "voice_minutes", "storage_mb"):
        used = (
            await session.execute(
                select(func.coalesce(func.sum(UsageMeter.quantity), 0)).where(
                    UsageMeter.tenant_id == tenant_id,
                    UsageMeter.kind == kind,
                    UsageMeter.occurred_at >= month_start,
                )
            )
        ).scalar_one()
        out[kind] = {"used": int(used or 0), "limit": limits.get(f"{kind}_month")}
    return out
