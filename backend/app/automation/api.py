"""Automation API: rules, journeys, SLA policies, notifications."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.models import (
    AutomationRule,
    Journey,
    JourneyInstance,
    Notification,
    SLAPolicy,
    SLATracker,
)
from app.core.db import get_session
from app.core.errors import NotFound
from app.core.permissions import AUTOMATION_READ, AUTOMATION_WRITE, require
from app.core.tenancy import AuthContext

router = APIRouter(prefix="/automation", tags=["automation"])


class RuleIn(BaseModel):
    name: str
    trigger_event: str
    conditions: dict[str, Any] = {}
    actions: list[dict[str, Any]]
    priority: int = 5


class JourneyIn(BaseModel):
    name: str
    trigger_event: str | None = None
    definition: list[dict[str, Any]]


class SLAPolicyIn(BaseModel):
    name: str
    entity_type: str
    trigger_event: str
    target_seconds: int
    warn_seconds: int | None = None
    escalations: list[dict[str, Any]] = []


@router.post("/rules", status_code=201)
async def create_rule(
    body: RuleIn,
    auth: AuthContext = Depends(require(AUTOMATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rule = AutomationRule(tenant_id=auth.tenant_id, **body.model_dump())
    session.add(rule)
    await session.flush()
    return {"id": str(rule.id), "name": rule.name}


@router.get("/rules")
async def list_rules(
    auth: AuthContext = Depends(require(AUTOMATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(AutomationRule).where(AutomationRule.tenant_id == auth.tenant_id)
        )
    ).scalars().all()
    return [
        {"id": str(r.id), "name": r.name, "trigger_event": r.trigger_event,
         "is_active": r.is_active, "actions": len(r.actions)}
        for r in rows
    ]


@router.post("/journeys", status_code=201)
async def create_journey(
    body: JourneyIn,
    auth: AuthContext = Depends(require(AUTOMATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    journey = Journey(tenant_id=auth.tenant_id, **body.model_dump())
    session.add(journey)
    await session.flush()
    return {"id": str(journey.id), "name": journey.name}


@router.get("/journeys")
async def list_journeys(
    auth: AuthContext = Depends(require(AUTOMATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(select(Journey).where(Journey.tenant_id == auth.tenant_id))
    ).scalars().all()
    return [
        {"id": str(j.id), "name": j.name, "trigger_event": j.trigger_event,
         "steps": len(j.definition), "is_active": j.is_active}
        for j in rows
    ]


@router.get("/journeys/instances")
async def list_instances(
    auth: AuthContext = Depends(require(AUTOMATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(JourneyInstance)
            .where(JourneyInstance.tenant_id == auth.tenant_id)
            .order_by(JourneyInstance.started_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return [
        {"id": str(i.id), "journey_id": str(i.journey_id), "entity_type": i.entity_type,
         "entity_id": str(i.entity_id), "status": i.status,
         "current_step": i.current_step,
         "wait_until": i.wait_until.isoformat() if i.wait_until else None}
        for i in rows
    ]


@router.post("/sla-policies", status_code=201)
async def create_sla_policy(
    body: SLAPolicyIn,
    auth: AuthContext = Depends(require(AUTOMATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    policy = SLAPolicy(tenant_id=auth.tenant_id, **body.model_dump())
    session.add(policy)
    await session.flush()
    return {"id": str(policy.id), "name": policy.name}


@router.get("/sla/trackers")
async def sla_trackers(
    auth: AuthContext = Depends(require(AUTOMATION_READ)),
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = select(SLATracker).where(SLATracker.tenant_id == auth.tenant_id)
    if status:
        query = query.where(SLATracker.status == status)
    rows = (
        await session.execute(query.order_by(SLATracker.due_at).limit(100))
    ).scalars().all()
    return [
        {"id": str(t.id), "entity_type": t.entity_type, "entity_id": str(t.entity_id),
         "status": t.status, "due_at": t.due_at.isoformat(),
         "breached_at": t.breached_at.isoformat() if t.breached_at else None}
        for t in rows
    ]


@router.get("/notifications")
async def list_notifications(
    auth: AuthContext = Depends(require(AUTOMATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Notification)
            .where(Notification.tenant_id == auth.tenant_id)
            .order_by(Notification.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [
        {"id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body,
         "status": n.status, "created_at": n.created_at.isoformat()}
        for n in rows
    ]
