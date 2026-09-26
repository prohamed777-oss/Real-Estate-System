"""Sales services (§26-35): opportunities, viewings, offers, reservations.

ReservationService (§35) is transactional, idempotent, concurrency-safe:
    Offer accepted → validation → FOR UPDATE inventory lock → inventory RESERVED
    → reservation row → audit → event. Double reservation is impossible (§102).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, IdempotencyConflict, NotFound, ValidationFailed
from app.events.outbox import emit
from app.leads.models import Lead
from app.properties.models import PropertyAsset, UnitInventory
from app.properties.service import mark_reserved
from app.sales.models import (
    NegotiationEntry,
    Offer,
    Opportunity,
    Reservation,
    Viewing,
    ViewingEvent,
)
from app.sales.statemachine import (
    OPPORTUNITY_EVENTS,
    OfferStateMachine,
    OpportunityStateMachine,
    ReservationStateMachine,
    ViewingStateMachine,
)

OFFER_APPROVAL_THRESHOLD_PCT = Decimal("5")  # discount >5% of asking → approval required (§59)


# ---------- opportunities ----------
async def create_opportunity(
    session: AsyncSession, *, tenant_id: uuid.UUID, lead_id: uuid.UUID, person_id: uuid.UUID,
    owner_id: uuid.UUID | None = None, property_interest: list | None = None,
    estimated_value: Decimal | None = None, actor_id=None,
) -> Opportunity:
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if lead is None:
        raise NotFound("Lead not found")
    opp = Opportunity(
        tenant_id=tenant_id, lead_id=lead_id, person_id=person_id,
        owner_id=owner_id or lead.owner_id, property_interest=property_interest or [],
        estimated_value=estimated_value, branch_id=lead.branch_id, team_id=lead.team_id,
    )
    session.add(opp)
    await session.flush()
    # Lead converts on opportunity creation (§9 CONVERTED via state machine)
    if lead.lifecycle_stage not in ("CONVERTED",):
        from app.leads.service import transition_lead
        from app.leads.statemachine import LeadStateMachine

        routes = {"NEW": "contact", "CONTACTED": "qualify", "QUALIFYING": "qualified",
                  "QUALIFIED": "convert", "NURTURE": "convert", "DORMANT": "reactivate"}
        path: list[str] = []
        state = lead.lifecycle_stage
        while state != "CONVERTED" and routes.get(state):
            event = routes[state]
            path.append(event)
            state = LeadStateMachine(state).transitions[state][event]
        for event in path:
            await transition_lead(session, lead=lead, event=event, actor_type="system",
                                  actor_id=actor_id, reason="opportunity created")
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="opportunity.created", entity_type="opportunity", entity_id=opp.id,
        after={"lead_id": str(lead_id)},
    )
    await emit(
        session, event_name="opportunity.created", tenant_id=tenant_id,
        aggregate_type="opportunity", aggregate_id=opp.id,
        payload={"opportunity_id": str(opp.id), "lead_id": str(lead_id)},
    )
    return opp


async def transition_opportunity(
    session: AsyncSession, *, opportunity: Opportunity, event: str, actor_type: str = "user",
    actor_id=None, reason: str | None = None,
) -> Opportunity:
    sm = OpportunityStateMachine(opportunity.stage)
    before = opportunity.stage
    sm.fire(event)
    opportunity.stage = sm.state
    if opportunity.stage in ("WON", "LOST"):
        opportunity.closed_at = datetime.now(UTC)
        opportunity.lost_reason = reason if opportunity.stage == "LOST" else None
    await audit(
        session, tenant_id=opportunity.tenant_id, actor_type=actor_type, actor_id=actor_id,
        action="opportunity.stage_changed", entity_type="opportunity", entity_id=opportunity.id,
        before={"stage": before}, after={"stage": opportunity.stage, "reason": reason},
    )
    await emit(
        session, event_name=OPPORTUNITY_EVENTS.get(event, "opportunity.stage_changed"),
        tenant_id=opportunity.tenant_id, aggregate_type="opportunity",
        aggregate_id=opportunity.id,
        payload={"opportunity_id": str(opportunity.id), "from": before, "to": opportunity.stage},
    )
    return opportunity


# ---------- viewings (§30-32) ----------
MIN_VIEWING_GAP_MINUTES = 30


async def create_viewing(
    session: AsyncSession, *, tenant_id: uuid.UUID, opportunity_id: uuid.UUID,
    asset_id: uuid.UUID, scheduled_at: datetime, salesperson_id: uuid.UUID | None = None,
    duration_minutes: int = 60, display_timezone: str = "Africa/Cairo",
    created_by: str | None = None, actor_id=None,
) -> Viewing:
    if scheduled_at.tzinfo is None:
        raise ValidationFailed("Viewing time must be timezone-aware (stored as UTC)")
    # Scheduling conflict check (§32): same salesperson, overlapping slot
    if salesperson_id:
        overlap = await session.execute(
            select(func.count()).select_from(Viewing).where(
                Viewing.tenant_id == tenant_id,
                Viewing.salesperson_id == salesperson_id,
                Viewing.status.in_(("REQUESTED", "CONFIRMED")),
                Viewing.scheduled_at < scheduled_at + timedelta(minutes=duration_minutes),
                Viewing.scheduled_at + func.make_interval(0, 0, 0, 0, 0, Viewing.duration_minutes)
                > scheduled_at,
            )
        )
        if (overlap.scalar_one() or 0) > 0:
            raise Conflict("Salesperson already has a viewing in this time slot")
    viewing = Viewing(
        tenant_id=tenant_id, opportunity_id=opportunity_id, asset_id=asset_id,
        salesperson_id=salesperson_id, scheduled_at=scheduled_at,
        duration_minutes=duration_minutes, display_timezone=display_timezone,
        created_by=created_by,
    )
    session.add(viewing)
    await session.flush()
    await emit(
        session, event_name="viewing.requested", tenant_id=tenant_id,
        aggregate_type="viewing", aggregate_id=viewing.id,
        payload={"viewing_id": str(viewing.id), "asset_id": str(asset_id),
                 "scheduled_at": scheduled_at.isoformat()},
    )
    return viewing


async def transition_viewing(
    session: AsyncSession, *, viewing: Viewing, event: str, actor_id=None,
    new_time: datetime | None = None, reason: str | None = None,
    feedback: dict | None = None,
) -> Viewing:
    sm = ViewingStateMachine(viewing.status)
    before_status = viewing.status
    before_time = viewing.scheduled_at
    sm.fire(event)
    viewing.status = sm.state
    kind = {"confirm": "confirmed", "attend": "confirmed", "complete": "completed",
            "reschedule": "rescheduled", "cancel": "cancelled", "no_show": "no_show"}.get(event, event)
    if event == "reschedule":
        if new_time is None:
            raise ValidationFailed("reschedule requires new_time")
        viewing.scheduled_at = new_time
    if event in ("complete", "attend") and feedback:
        viewing.feedback = feedback
    session.add(
        ViewingEvent(
            tenant_id=viewing.tenant_id, viewing_id=viewing.id, kind=kind,
            from_at=before_time, to_at=viewing.scheduled_at, actor_id=str(actor_id) if actor_id else None,
            reason=reason,
        )
    )
    event_names = {"confirm": "viewing.confirmed", "reschedule": "viewing.rescheduled",
                   "cancel": "viewing.cancelled", "no_show": "viewing.no_show",
                   "complete": "viewing.completed", "attend": "viewing.confirmed"}
    await emit(
        session, event_name=event_names.get(event, "viewing.updated"), tenant_id=viewing.tenant_id,
        aggregate_type="viewing", aggregate_id=viewing.id,
        payload={"viewing_id": str(viewing.id), "status": viewing.status,
                 "feedback": feedback or {}},
    )
    return viewing


# ---------- offers (§33-34) ----------
async def create_offer(
    session: AsyncSession, *, tenant_id: uuid.UUID, opportunity_id: uuid.UUID,
    asset_id: uuid.UUID, price_amount: Decimal | str, price_currency: str = "EGP",
    payment_plan_snapshot: dict | None = None, terms: dict | None = None,
    validity_days: int = 14, actor_id=None, actor_role: str = "sales",
) -> Offer:
    opp = (
        await session.execute(
            select(Opportunity).where(
                Opportunity.id == opportunity_id, Opportunity.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if opp is None:
        raise NotFound("Opportunity not found")
    from app.properties.service import get_current_price

    current_price = await get_current_price(session, tenant_id, asset_id)
    asking = current_price.amount if current_price else None
    discount = {}
    approval_required = False
    if asking is not None and Decimal(str(price_amount)) < asking:
        discount_pct = ((asking - Decimal(str(price_amount))) / asking) * 100
        discount = {"asking_amount": str(asking), "discount_pct": str(round(discount_pct, 2))}
        approval_required = discount_pct > OFFER_APPROVAL_THRESHOLD_PCT
        if approval_required and actor_role not in ("sales_manager", "owner", "admin"):
            raise ValidationFailed(
                "Discount above 5% requires manager approval",
                details={"discount_pct": str(round(discount_pct, 2))},
            )
    version = 1
    last_offer = (
        await session.execute(
            select(func.max(Offer.version)).where(
                Offer.tenant_id == tenant_id, Offer.opportunity_id == opportunity_id
            )
        )
    ).scalar_one()
    if last_offer:
        version = int(last_offer) + 1
    offer = Offer(
        tenant_id=tenant_id, opportunity_id=opportunity_id, asset_id=asset_id,
        version=version, price_amount=Decimal(str(price_amount)), price_currency=price_currency,
        payment_plan_snapshot=payment_plan_snapshot or {}, price_snapshot={
            "price_version_id": str(current_price.id) if current_price else None,
            "asking_amount": str(asking) if asking else None,
            "captured_at": datetime.now(UTC).isoformat(),
        },
        discount=discount, terms=terms or {},
        validity_until=datetime.now(UTC) + timedelta(days=validity_days),
        approval_required=approval_required, created_by=str(actor_id) if actor_id else None,
    )
    session.add(offer)
    await session.flush()
    session.add(
        NegotiationEntry(
            tenant_id=tenant_id, offer_id=offer.id, kind="offer",
            payload={"amount": str(offer.price_amount), "version": version},
            actor_type="user", actor_id=str(actor_id) if actor_id else None,
        )
    )
    await emit(
        session, event_name="offer.created", tenant_id=tenant_id, aggregate_type="offer",
        aggregate_id=offer.id,
        payload={"offer_id": str(offer.id), "opportunity_id": str(opportunity_id),
                 "version": version, "amount": str(offer.price_amount)},
    )
    return offer


async def transition_offer(
    session: AsyncSession, *, offer: Offer, event: str, actor_id=None, reason: str | None = None,
) -> Offer:
    sm = OfferStateMachine(offer.status)
    before = offer.status
    sm.fire(event)
    offer.status = sm.state
    now = datetime.now(UTC)
    if event == "send":
        offer.sent_at = now
    if event == "accept":
        offer.responded_at = now
    if event == "reject":
        offer.rejection_reason = reason
        offer.responded_at = now
    if event == "approve":
        offer.approved_by = actor_id if isinstance(actor_id, uuid.UUID) else None
        offer.approved_at = now
    session.add(
        NegotiationEntry(
            tenant_id=offer.tenant_id, offer_id=offer.id, kind=event,
            payload={"from": before, "to": offer.status, "reason": reason},
            actor_type="user", actor_id=str(actor_id) if actor_id else None,
        )
    )
    event_names = {"send": "offer.sent", "accept": "offer.accepted", "reject": "offer.rejected",
                   "counter": "offer.countered", "approve": "offer.approved"}
    await emit(
        session, event_name=event_names.get(event, "offer.updated"), tenant_id=offer.tenant_id,
        aggregate_type="offer", aggregate_id=offer.id,
        payload={"offer_id": str(offer.id), "from": before, "to": offer.status},
    )
    return offer


# ---------- reservations (§35) — the most protected operation ----------
async def create_reservation(
    session: AsyncSession, *, tenant_id: uuid.UUID, opportunity_id: uuid.UUID,
    offer_id: uuid.UUID, idempotency_key: str | None = None, deposit_amount: Decimal | None = None,
    expires_in_hours: int = 72, actor_type: str = "user", actor_id=None,
) -> Reservation:
    """All-or-nothing: inventory lock + reservation + audit + event in ONE tx."""
    # Idempotency (§1.7): same key → same reservation, never a duplicate
    if idempotency_key:
        existing = (
            await session.execute(
                select(Reservation).where(
                    Reservation.tenant_id == tenant_id,
                    Reservation.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    offer = (
        await session.execute(
            select(Offer).where(Offer.id == offer_id, Offer.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if offer is None:
        raise NotFound("Offer not found")
    if offer.status != "ACCEPTED":
        raise Conflict("Reservation requires an ACCEPTED offer",
                       details={"offer_status": offer.status})
    if offer.validity_until and offer.validity_until < datetime.now(UTC):
        raise Conflict("Offer validity expired")

    asset = (
        await session.execute(
            select(PropertyAsset).where(
                PropertyAsset.id == offer.asset_id, PropertyAsset.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise NotFound("Asset not found")
    if asset.asset_type != "unit":
        raise ValidationFailed("Only unit assets can be reserved")

    # Concurrency-safe inventory transition (§18, §102) — raises Conflict if taken
    reservation = Reservation(
        tenant_id=tenant_id, opportunity_id=opportunity_id, offer_id=offer_id,
        asset_id=offer.asset_id, expires_at=datetime.now(UTC) + timedelta(hours=expires_in_hours),
        deposit_amount=deposit_amount,
        offer_snapshot={
            "offer_id": str(offer.id), "version": offer.version,
            "price_amount": str(offer.price_amount), "price_currency": offer.price_currency,
            "payment_plan_snapshot": offer.payment_plan_snapshot,
            "price_snapshot": offer.price_snapshot,
        },
        idempotency_key=idempotency_key,
        created_by=str(actor_id) if actor_id else None,
    )
    session.add(reservation)
    await session.flush()
    await mark_reserved(
        session, tenant_id=tenant_id, asset_id=offer.asset_id, reservation_id=reservation.id,
        actor_type=actor_type, actor_id=actor_id,
    )
    await audit(
        session, tenant_id=tenant_id, actor_type=actor_type, actor_id=actor_id,
        action="reservation.created", entity_type="reservation", entity_id=reservation.id,
        after={"asset_id": str(offer.asset_id), "offer_id": str(offer_id)},
    )
    await emit(
        session, event_name="reservation.created", tenant_id=tenant_id,
        aggregate_type="reservation", aggregate_id=reservation.id,
        payload={"reservation_id": str(reservation.id), "asset_id": str(offer.asset_id),
                 "offer_id": str(offer_id), "opportunity_id": str(opportunity_id)},
    )
    return reservation


async def transition_reservation(
    session: AsyncSession, *, reservation: Reservation, event: str, actor_id=None,
    reason: str | None = None,
) -> Reservation:
    sm = ReservationStateMachine(reservation.status)
    before = reservation.status
    sm.fire(event)
    reservation.status = sm.state
    if event in ("cancel", "expire"):
        # Free the inventory back (§17 RELEASED path)
        from app.properties.service import release_unit

        await release_unit(session, tenant_id=reservation.tenant_id,
                           asset_id=reservation.asset_id, actor_id=actor_id, reason=reason)
    if event == "convert":
        reservation.converted_contract_id = reservation.converted_contract_id
    await audit(
        session, tenant_id=reservation.tenant_id, actor_type="user", actor_id=actor_id,
        action="reservation.cancelled" if event == "cancel" else "reservation.updated",
        entity_type="reservation", entity_id=reservation.id,
        before={"status": before}, after={"status": reservation.status, "reason": reason},
    )
    event_names = {"cancel": "reservation.cancelled", "expire": "reservation.expired",
                   "convert": "reservation.converted", "confirm": "reservation.confirmed"}
    await emit(
        session, event_name=event_names.get(event, "reservation.updated"),
        tenant_id=reservation.tenant_id, aggregate_type="reservation",
        aggregate_id=reservation.id,
        payload={"reservation_id": str(reservation.id), "from": before,
                 "to": reservation.status, "reason": reason},
    )
    return reservation


async def generate_offer_document(
    session: AsyncSession, *, tenant_id: uuid.UUID, offer_id: uuid.UUID, actor_id=None,
) -> dict[str, Any]:
    """Render an RTL offer document from the IMMUTABLE offer snapshot and file it
    as a managed Document (§36) — an offer worth sending is a document, not text."""
    offer = (
        await session.execute(
            select(Offer).where(Offer.id == offer_id, Offer.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if offer is None:
        raise NotFound("Offer not found")
    captured = (offer.price_snapshot or {}).get("captured_at", "")
    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>عرض سعر {offer.version}</title>
<style>
body{{font-family:'Cairo',sans-serif;padding:40px;color:#0f172a}}
.card{{border:1px solid #e2e8f0;border-radius:16px;padding:24px;max-width:720px}}
.price{{font-size:28px;font-weight:800;color:#047857}}
table{{width:100%;border-collapse:collapse;margin-top:12px}}
td,th{{border:1px solid #e2e8f0;padding:8px;text-align:start;font-size:14px}}
</style></head><body>
<div class="card">
<h1>عرض سعر عقاري — الإصدار {offer.version}</h1>
<table>
<tr><th>السعر</th><td class="price">{offer.price_amount:,.0f} {offer.price_currency}</td></tr>
<tr><th>خطة السداد</th><td>{offer.payment_plan_snapshot or 'نقدي / حسب الاتفاق'}</td></tr>
<tr><th>الشروط</th><td>{offer.terms or '—'}</td></tr>
<tr><th>صالح حتى</th><td>{offer.validity_until.strftime('%Y-%m-%d') if offer.validity_until else '—'}</td></tr>
<tr><th>حالة العرض</th><td>{offer.status}</td></tr>
</table>
<p style="margin-top:16px;font-size:12px;color:#64748b">
هذا العرض مُولَّد آليًا من نظام إيرادات العقارات، ويحمل نسخة سعر ثابتة ({captured}).
</p>
</div></body></html>"""
    from app.finance.documents_service import create_document

    doc = await create_document(
        session, tenant_id=tenant_id, kind="offer",
        title=f"عرض سعر v{offer.version} — {str(offer_id)[:8]}",
        entity_type="offer", entity_id=offer_id, actor_id=actor_id,
    )
    doc.metadata_ = {"html": html, "generated": True}
    await session.flush()
    return {"document_id": str(doc.id), "status": doc.status, "html_length": len(html)}
