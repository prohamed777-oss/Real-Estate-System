"""Leads API: CRUD-lite + lifecycle transitions + requirements + inbox queries."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ValidationFailed
from app.core.pagination import CursorPage, encode_cursor
from app.core.permissions import LEADS_READ, LEADS_WRITE, require
from app.core.tenancy import AuthContext
from app.identity.models import Person
from app.leads.models import Lead, LeadRequirement
from app.leads.service import (
    assign_lead,
    create_lead,
    get_lead,
    recompute_scores,
    transition_lead,
    update_requirements,
)

router = APIRouter(prefix="/leads", tags=["leads"])


class LeadIn(BaseModel):
    person_id: uuid.UUID | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str | None = None
    explicit_requirements: dict[str, Any] = {}


class TransitionIn(BaseModel):
    event: str
    reason: str | None = None


class AssignIn(BaseModel):
    owner_id: uuid.UUID
    team_id: uuid.UUID | None = None
    reason: str | None = None


class RequirementsIn(BaseModel):
    explicit: dict[str, Any] | None = None
    behavioral: dict[str, Any] | None = None
    objections: list | None = None
    timeline: str | None = None
    confidence: int | None = None


def _lead_dict(lead: Lead, person: Person | None = None) -> dict[str, Any]:
    return {
        "id": str(lead.id),
        "person_id": str(lead.person_id),
        "person_name": person.full_name if person else None,
        "person_phone": person.phone if person else None,
        "source": lead.source,
        "owner_id": str(lead.owner_id) if lead.owner_id else None,
        "lifecycle_stage": lead.lifecycle_stage,
        "lead_score": lead.lead_score,
        "intent_score": lead.intent_score,
        "engagement_score": lead.engagement_score,
        "fit_score": lead.fit_score,
        "score_version": lead.score_version,
        "next_action": lead.next_action,
        "next_action_at": lead.next_action_at.isoformat() if lead.next_action_at else None,
        "dormant_since": lead.dormant_since.isoformat() if lead.dormant_since else None,
        "created_at": lead.created_at.isoformat(),
    }


@router.get("")
async def list_leads(
    auth: AuthContext = Depends(require(LEADS_READ)),
    session: AsyncSession = Depends(get_session),
    stage: str | None = Query(default=None, alias="stage"),
    owner_id: uuid.UUID | None = None,
    min_score: int | None = None,
    q: str | None = None,
    limit: int = Query(default=50, le=200),
    cursor: str | None = None,
) -> CursorPage:
    query = (
        select(Lead, Person)
        .join(Person, Person.id == Lead.person_id)
        .where(Lead.tenant_id == auth.tenant_id)
        .order_by(Lead.created_at.desc(), Lead.id.desc())
    )
    # Branch/Team/Ownership scoping (§93): sales sees only their own leads
    if auth.role_key in ("sales",):
        query = query.where(Lead.owner_id == auth.user_id)
    if stage:
        query = query.where(Lead.lifecycle_stage == stage)
    if owner_id:
        query = query.where(Lead.owner_id == owner_id)
    if min_score is not None:
        query = query.where(Lead.lead_score >= min_score)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(func.lower(Person.full_name).like(like) | func.lower(func.coalesce(Person.phone, "")).like(like))
    rows = (await session.execute(query.limit(limit))).all()
    items = [_lead_dict(lead, person) for lead, person in rows]
    next_cursor = (
        encode_cursor(created_at=rows[-1][0].created_at, id_=rows[-1][0].id) if len(items) == limit and rows else None
    )
    return CursorPage(items=items, next_cursor=next_cursor, has_more=bool(next_cursor))


@router.post("", status_code=201)
async def create(
    body: LeadIn,
    auth: AuthContext = Depends(require(LEADS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if body.person_id:
        person_id = body.person_id
    elif body.full_name or body.phone or body.email:
        from app.organizations.provisioning import get_or_create_person

        person = await get_or_create_person(
            session, tenant_id=auth.tenant_id, full_name=body.full_name or "",
            email=body.email, phone=body.phone,
        )
        person_id = person.id
    else:
        raise ValidationFailed("person_id or contact info required")
    lead = await create_lead(
        session, tenant_id=auth.tenant_id, person_id=person_id, source=body.source,
        created_by="user", actor_id=auth.user_id,
        explicit_requirements=body.explicit_requirements,
    )
    await recompute_scores(session, lead=lead)
    return _lead_dict(lead)


@router.get("/{lead_id}")
async def get_one(
    lead_id: uuid.UUID,
    auth: AuthContext = Depends(require(LEADS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    lead = await get_lead(session, auth.tenant_id, lead_id)
    person = await session.get(Person, lead.person_id)
    req = (
        await session.execute(select(LeadRequirement).where(LeadRequirement.lead_id == lead_id))
    ).scalar_one_or_none()
    from app.leads.statemachine import LeadStateMachine

    sm = LeadStateMachine(lead.lifecycle_stage)
    return {
        **_lead_dict(lead, person),
        "requirements": {
            "explicit": req.explicit if req else {},
            "behavioral": req.behavioral if req else {},
            "objections": req.objections if req else [],
            "timeline": req.timeline if req else None,
            "confidence": req.confidence if req else 0,
        },
        "allowed_events": sm.allowed_events(),
        "score_signals": lead.score_signals or {},
    }


@router.post("/{lead_id}/transition")
async def transition(
    lead_id: uuid.UUID,
    body: TransitionIn,
    auth: AuthContext = Depends(require(LEADS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    lead = await get_lead(session, auth.tenant_id, lead_id)
    await transition_lead(
        session, lead=lead, event=body.event, actor_type="user", actor_id=auth.user_id,
        reason=body.reason,
    )
    return {"id": str(lead.id), "lifecycle_stage": lead.lifecycle_stage}


@router.post("/{lead_id}/assign")
async def assign(
    lead_id: uuid.UUID,
    body: AssignIn,
    auth: AuthContext = Depends(require(LEADS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    lead = await get_lead(session, auth.tenant_id, lead_id)
    await assign_lead(
        session, lead=lead, owner_id=body.owner_id, team_id=body.team_id,
        actor_type="user", actor_id=auth.user_id, reason=body.reason,
    )
    return {"id": str(lead.id), "owner_id": str(lead.owner_id)}


@router.put("/{lead_id}/requirements")
async def put_requirements(
    lead_id: uuid.UUID,
    body: RequirementsIn,
    auth: AuthContext = Depends(require(LEADS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await get_lead(session, auth.tenant_id, lead_id)
    req = await update_requirements(
        session, lead_id=lead_id, explicit=body.explicit, behavioral=body.behavioral,
        objections=body.objections, timeline=body.timeline, confidence=body.confidence,
        source="manual",
    )
    return {"explicit": req.explicit, "behavioral": req.behavioral, "timeline": req.timeline}


@router.post("/{lead_id}/rescore")
async def rescore(
    lead_id: uuid.UUID,
    auth: AuthContext = Depends(require(LEADS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    lead = await get_lead(session, auth.tenant_id, lead_id)
    await recompute_scores(session, lead=lead)
    return {
        "lead_score": lead.lead_score, "intent_score": lead.intent_score,
        "engagement_score": lead.engagement_score, "fit_score": lead.fit_score,
        "signals": lead.score_signals,
    }
