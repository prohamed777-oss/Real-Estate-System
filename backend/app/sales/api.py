"""Sales API: opportunities, viewings, offers, reservations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFound
from app.core.idempotency import IdempotencyGuard
from app.core.permissions import (
    OFFERS_READ,
    OFFERS_WRITE,
    OPPORTUNITIES_READ,
    OPPORTUNITIES_WRITE,
    RESERVATIONS_READ,
    RESERVATIONS_WRITE,
    VIEWINGS_READ,
    VIEWINGS_WRITE,
    require,
)
from app.core.tenancy import AuthContext
from app.sales.models import Offer, NegotiationEntry, Opportunity, Reservation, Viewing, ViewingEvent
from app.sales.service import (
    create_offer,
    create_opportunity,
    create_reservation,
    create_viewing,
    transition_offer,
    transition_opportunity,
    transition_reservation,
    transition_viewing,
)
from app.sales.statemachine import OpportunityStateMachine, ViewingStateMachine

router = APIRouter(tags=["sales"])


# ---------- opportunities ----------
class OpportunityIn(BaseModel):
    lead_id: uuid.UUID
    person_id: uuid.UUID | None = None
    property_interest: list[str] = []
    estimated_value: Decimal | None = None


class OpportunityTransitionIn(BaseModel):
    event: str
    reason: str | None = None


@router.post("/opportunities", status_code=201)
async def post_opportunity(
    body: OpportunityIn,
    auth: AuthContext = Depends(require(OPPORTUNITIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.leads.models import Lead

    lead = (
        await session.execute(select(Lead).where(Lead.id == body.lead_id, Lead.tenant_id == auth.tenant_id))
    ).scalar_one_or_none()
    if lead is None:
        raise NotFound("Lead not found")
    opp = await create_opportunity(
        session, tenant_id=auth.tenant_id, lead_id=body.lead_id,
        person_id=body.person_id or lead.person_id,
        property_interest=body.property_interest, estimated_value=body.estimated_value,
        actor_id=auth.user_id,
    )
    return {"id": str(opp.id), "stage": opp.stage, "lead_id": str(opp.lead_id)}


@router.get("/opportunities")
async def list_opportunities(
    auth: AuthContext = Depends(require(OPPORTUNITIES_READ)),
    session: AsyncSession = Depends(get_session),
    stage: str | None = None,
    mine: bool = False,
    limit: int = Query(default=50, le=200),
) -> list[dict[str, Any]]:
    query = select(Opportunity).where(Opportunity.tenant_id == auth.tenant_id)
    if stage:
        query = query.where(Opportunity.stage == stage)
    if mine:
        query = query.where(Opportunity.owner_id == auth.user_id)
    # Branch/Team scoping: sales sees only their own opportunities
    if auth.role_key == "sales" and not mine:
        query = query.where(Opportunity.owner_id == auth.user_id)
    rows = (await session.execute(query.order_by(Opportunity.created_at.desc()).limit(limit))).scalars().all()
    return [
        {
            "id": str(o.id), "lead_id": str(o.lead_id), "person_id": str(o.person_id),
            "stage": o.stage, "owner_id": str(o.owner_id) if o.owner_id else None,
            "estimated_value": str(o.estimated_value) if o.estimated_value else None,
            "currency": o.currency, "probability": o.probability,
            "property_interest": o.property_interest,
            "closed_at": o.closed_at.isoformat() if o.closed_at else None,
            "created_at": o.created_at.isoformat(),
        }
        for o in rows
    ]


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(
    opportunity_id: uuid.UUID,
    auth: AuthContext = Depends(require(OPPORTUNITIES_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    opp = (
        await session.execute(
            select(Opportunity).where(
                Opportunity.id == opportunity_id, Opportunity.tenant_id == auth.tenant_id
            )
        )
    ).scalar_one_or_none()
    if opp is None:
        raise NotFound("Opportunity not found")
    sm = OpportunityStateMachine(opp.stage)
    offers = (
        await session.execute(
            select(Offer).where(Offer.opportunity_id == opportunity_id).order_by(Offer.version)
        )
    ).scalars().all()
    return {
        "id": str(opp.id), "stage": opp.stage, "lead_id": str(opp.lead_id),
        "allowed_events": sm.allowed_events(), "offers": [
            {"id": str(o.id), "version": o.version, "status": o.status,
             "amount": str(o.price_amount), "currency": o.price_currency}
            for o in offers
        ],
    }


@router.post("/opportunities/{opportunity_id}/transition")
async def do_opportunity_transition(
    opportunity_id: uuid.UUID,
    body: OpportunityTransitionIn,
    auth: AuthContext = Depends(require(OPPORTUNITIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    opp = (
        await session.execute(
            select(Opportunity).where(
                Opportunity.id == opportunity_id, Opportunity.tenant_id == auth.tenant_id
            )
        )
    ).scalar_one_or_none()
    if opp is None:
        raise NotFound("Opportunity not found")
    await transition_opportunity(session, opportunity=opp, event=body.event,
                                 actor_id=auth.user_id, reason=body.reason)
    return {"id": str(opp.id), "stage": opp.stage}


# ---------- viewings ----------
class ViewingIn(BaseModel):
    opportunity_id: uuid.UUID
    asset_id: uuid.UUID
    scheduled_at: str  # ISO with timezone
    duration_minutes: int = 60
    salesperson_id: uuid.UUID | None = None


class ViewingTransitionIn(BaseModel):
    event: str  # confirm | reschedule | cancel | no_show | complete
    new_time: str | None = None
    reason: str | None = None
    feedback: dict[str, Any] | None = None


@router.post("/viewings", status_code=201)
async def post_viewing(
    body: ViewingIn,
    auth: AuthContext = Depends(require(VIEWINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    viewing = await create_viewing(
        session, tenant_id=auth.tenant_id, opportunity_id=body.opportunity_id,
        asset_id=body.asset_id, scheduled_at=datetime.fromisoformat(body.scheduled_at),
        salesperson_id=body.salesperson_id or auth.user_id,
        duration_minutes=body.duration_minutes, created_by=str(auth.user_id),
        actor_id=auth.user_id,
    )
    return {"id": str(viewing.id), "status": viewing.status,
            "scheduled_at": viewing.scheduled_at.isoformat()}


@router.get("/viewings")
async def list_viewings(
    auth: AuthContext = Depends(require(VIEWINGS_READ)),
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
    mine: bool = False,
) -> list[dict[str, Any]]:
    query = select(Viewing).where(Viewing.tenant_id == auth.tenant_id)
    if status:
        query = query.where(Viewing.status == status)
    if mine:
        query = query.where(Viewing.salesperson_id == auth.user_id)
    rows = (await session.execute(query.order_by(Viewing.scheduled_at).limit(100))).scalars().all()
    return [
        {
            "id": str(v.id), "opportunity_id": str(v.opportunity_id), "asset_id": str(v.asset_id),
            "scheduled_at": v.scheduled_at.isoformat(), "status": v.status,
            "salesperson_id": str(v.salesperson_id) if v.salesperson_id else None,
            "display_timezone": v.display_timezone,
        }
        for v in rows
    ]


@router.post("/viewings/{viewing_id}/transition")
async def do_viewing_transition(
    viewing_id: uuid.UUID,
    body: ViewingTransitionIn,
    auth: AuthContext = Depends(require(VIEWINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    viewing = (
        await session.execute(
            select(Viewing).where(Viewing.id == viewing_id, Viewing.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if viewing is None:
        raise NotFound("Viewing not found")
    await transition_viewing(
        session, viewing=viewing, event=body.event, actor_id=auth.user_id,
        new_time=datetime.fromisoformat(body.new_time) if body.new_time else None,
        reason=body.reason, feedback=body.feedback,
    )
    return {"id": str(viewing.id), "status": viewing.status,
            "scheduled_at": viewing.scheduled_at.isoformat()}


# ---------- offers ----------
class OfferIn(BaseModel):
    opportunity_id: uuid.UUID
    asset_id: uuid.UUID
    price_amount: Decimal
    price_currency: str = "EGP"
    payment_plan_snapshot: dict[str, Any] = {}
    terms: dict[str, Any] = {}
    validity_days: int = 14


class OfferTransitionIn(BaseModel):
    event: str  # submit_approval | approve | send | accept | reject | counter | expire | withdraw
    reason: str | None = None


@router.post("/offers", status_code=201)
async def post_offer(
    body: OfferIn,
    auth: AuthContext = Depends(require(OFFERS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    offer = await create_offer(
        session, tenant_id=auth.tenant_id, opportunity_id=body.opportunity_id,
        asset_id=body.asset_id, price_amount=body.price_amount,
        price_currency=body.price_currency, payment_plan_snapshot=body.payment_plan_snapshot,
        terms=body.terms, validity_days=body.validity_days, actor_id=auth.user_id,
    )
    return {
        "id": str(offer.id), "version": offer.version, "status": offer.status,
        "approval_required": offer.approval_required,
        "price": str(offer.price_amount), "currency": offer.price_currency,
    }


@router.post("/offers/{offer_id}/transition")
async def do_offer_transition(
    offer_id: uuid.UUID,
    body: OfferTransitionIn,
    auth: AuthContext = Depends(require(OFFERS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    offer = (
        await session.execute(
            select(Offer).where(Offer.id == offer_id, Offer.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if offer is None:
        raise NotFound("Offer not found")
    if body.event == "approve":
        auth.require("offers:approve")
    await transition_offer(session, offer=offer, event=body.event, actor_id=auth.user_id,
                           reason=body.reason)
    return {"id": str(offer.id), "status": offer.status}


@router.get("/offers/{offer_id}/negotiation")
async def negotiation_history(
    offer_id: uuid.UUID,
    auth: AuthContext = Depends(require(OFFERS_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(NegotiationEntry)
            .where(NegotiationEntry.offer_id == offer_id, NegotiationEntry.tenant_id == auth.tenant_id)
            .order_by(NegotiationEntry.created_at)
        )
    ).scalars().all()
    return [
        {"id": str(n.id), "kind": n.kind, "payload": n.payload, "actor_type": n.actor_type,
         "created_at": n.created_at.isoformat()}
        for n in rows
    ]


# ---------- reservations ----------
class ReservationIn(BaseModel):
    opportunity_id: uuid.UUID
    offer_id: uuid.UUID
    deposit_amount: Decimal | None = None
    expires_in_hours: int = 72


@router.post("/reservations", status_code=201)
async def post_reservation(
    body: ReservationIn,
    auth: AuthContext = Depends(require(RESERVATIONS_WRITE)),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Query(default=None),
) -> dict[str, Any]:
    guard = IdempotencyGuard(session, auth.tenant_id)
    replay = await guard.begin(idempotency_key, body.model_dump(mode="json"))
    if replay is not None:
        return replay
    reservation = await create_reservation(
        session, tenant_id=auth.tenant_id, opportunity_id=body.opportunity_id,
        offer_id=body.offer_id, deposit_amount=body.deposit_amount,
        expires_in_hours=body.expires_in_hours, actor_type="user", actor_id=auth.user_id,
    )
    response = {
        "id": str(reservation.id), "status": reservation.status,
        "asset_id": str(reservation.asset_id),
        "expires_at": reservation.expires_at.isoformat() if reservation.expires_at else None,
    }
    await guard.commit(response)
    return response


@router.get("/reservations")
async def list_reservations(
    auth: AuthContext = Depends(require(RESERVATIONS_READ)),
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = select(Reservation).where(Reservation.tenant_id == auth.tenant_id)
    if status:
        query = query.where(Reservation.status == status)
    rows = (await session.execute(query.order_by(Reservation.reserved_at.desc()).limit(100))).scalars().all()
    return [
        {
            "id": str(r.id), "asset_id": str(r.asset_id), "offer_id": str(r.offer_id),
            "status": r.status, "reserved_at": r.reserved_at.isoformat(),
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "deposit": str(r.deposit_amount) if r.deposit_amount else None,
        }
        for r in rows
    ]


@router.post("/reservations/{reservation_id}/transition")
async def do_reservation_transition(
    reservation_id: uuid.UUID,
    body: dict[str, str],
    auth: AuthContext = Depends(require(RESERVATIONS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    reservation = (
        await session.execute(
            select(Reservation).where(
                Reservation.id == reservation_id, Reservation.tenant_id == auth.tenant_id
            )
        )
    ).scalar_one_or_none()
    if reservation is None:
        raise NotFound("Reservation not found")
    await transition_reservation(
        session, reservation=reservation, event=body["event"], actor_id=auth.user_id,
        reason=body.get("reason"),
    )
    return {"id": str(reservation.id), "status": reservation.status}


@router.post("/offers/{offer_id}/document", status_code=201)
async def post_offer_document(
    offer_id: uuid.UUID,
    auth: AuthContext = Depends(require(OFFERS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Generate the RTL offer document from the immutable snapshot (§36)."""
    from app.sales.service import generate_offer_document

    return await generate_offer_document(
        session, tenant_id=auth.tenant_id, offer_id=offer_id, actor_id=auth.user_id,
    )
