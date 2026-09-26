"""Sales event handlers: opportunity/viewing/reservation automation hooks (§112)."""

from __future__ import annotations

import uuid

from app.events.registry import event_handler


@event_handler("viewing.completed")
async def on_viewing_completed(session, envelope):  # noqa: ANN001
    """Viewing completed → advance opportunity stage (§112)."""
    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from sqlalchemy import select

    from app.sales.models import Opportunity, Viewing
    from app.sales.service import transition_opportunity

    viewing = (
        await session.execute(
            select(Viewing).where(Viewing.id == uuid.UUID(payload["viewing_id"]))
        )
    ).scalar_one_or_none()
    if viewing is None:
        return
    opp = (
        await session.execute(
            select(Opportunity).where(Opportunity.id == viewing.opportunity_id)
        )
    ).scalar_one_or_none()
    if opp is None or opp.stage != "VIEWING":
        return
    await transition_opportunity(
        session, opportunity=opp, event="offer", actor_type="automation",
        reason="viewing completed",
    )


@event_handler("offer.accepted")
async def on_offer_accepted(session, envelope):  # noqa: ANN001
    """Accepted offer → opportunity moves to NEGOTIATION/RESERVATION stage (§112)."""
    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from sqlalchemy import select

    from app.sales.models import Offer, Opportunity
    from app.sales.service import transition_opportunity

    offer = (
        await session.execute(select(Offer).where(Offer.id == uuid.UUID(payload["offer_id"])))
    ).scalar_one_or_none()
    if offer is None:
        return
    opp = (
        await session.execute(
            select(Opportunity).where(Opportunity.id == offer.opportunity_id)
        )
    ).scalar_one_or_none()
    if opp is None:
        return
    from app.sales.statemachine import OpportunityStateMachine

    sm = OpportunityStateMachine(opp.stage)
    if "negotiate" in sm.allowed_events():
        await transition_opportunity(session, opportunity=opp, event="negotiate",
                                     actor_type="automation", reason="offer accepted")


@event_handler("reservation.created")
async def on_reservation_created(session, envelope):  # noqa: ANN001
    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from sqlalchemy import select

    from app.sales.models import Opportunity, Reservation
    from app.sales.service import transition_opportunity

    reservation = (
        await session.execute(
            select(Reservation).where(Reservation.id == uuid.UUID(payload["reservation_id"]))
        )
    ).scalar_one_or_none()
    if reservation is None:
        return
    opp = (
        await session.execute(
            select(Opportunity).where(Opportunity.id == reservation.opportunity_id)
        )
    ).scalar_one_or_none()
    if opp is None:
        return
    from app.sales.statemachine import OpportunityStateMachine

    sm = OpportunityStateMachine(opp.stage)
    if "reserve" in sm.allowed_events():
        await transition_opportunity(session, opportunity=opp, event="reserve",
                                     actor_type="automation", reason="reservation created")


@event_handler("reservation.expired")
async def on_reservation_expired(session, envelope):  # noqa: ANN001
    return None  # inventory release handled by transition; notification lands in M8
