"""Finance services (§37-39): deals, payments, commission engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.events.outbox import emit
from app.finance.models import (
    Commission,
    CommissionRule,
    Contract,
    Deal,
    Payment,
    PaymentSchedule,
)
from app.sales.models import Offer, Opportunity, Reservation


async def create_contract_from_reservation(
    session: AsyncSession, *, tenant_id: uuid.UUID, reservation_id: uuid.UUID,
    document_id: uuid.UUID | None = None, actor_id=None,
) -> Contract:
    res = (
        await session.execute(
            select(Reservation).where(
                Reservation.id == reservation_id, Reservation.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if res is None:
        raise NotFound("Reservation not found")
    if res.status not in ("ACTIVE", "CONFIRMED"):
        raise Conflict(f"Cannot contract a {res.status} reservation")
    contract = Contract(
        tenant_id=tenant_id, opportunity_id=res.opportunity_id,
        reservation_id=reservation_id, asset_id=res.asset_id,
        type="contract", status="pending_signature",
        document_id=document_id,
        financial_snapshot=res.offer_snapshot,
        created_by=str(actor_id) if actor_id else None,
    )
    session.add(contract)
    await session.flush()
    # Inventory: RESERVED → CONTRACTED (§17)
    from app.properties.service import mark_contracted

    await mark_contracted(
        session, tenant_id=tenant_id, asset_id=res.asset_id, contract_id=contract.id,
        actor_id=actor_id,
    )
    # Reservation → CONVERTED
    from app.sales.service import transition_reservation

    await transition_reservation(
        session, reservation=res, event="convert", actor_id=actor_id,
        reason="contract created",
    )
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="contract.created", entity_type="contract", entity_id=contract.id,
        after={"reservation_id": str(reservation_id)},
    )
    await emit(
        session, event_name="contract.created", tenant_id=tenant_id,
        aggregate_type="contract", aggregate_id=contract.id,
        payload={"contract_id": str(contract.id), "reservation_id": str(reservation_id)},
    )
    return contract


async def create_deal(
    session: AsyncSession, *, tenant_id: uuid.UUID, opportunity_id: uuid.UUID,
    contract_id: uuid.UUID | None = None, gross_value: Decimal | None = None,
    currency: str = "EGP", actor_id=None,
) -> Deal:
    opp = (
        await session.execute(
            select(Opportunity).where(
                Opportunity.id == opportunity_id, Opportunity.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if opp is None:
        raise NotFound("Opportunity not found")
    deal = Deal(
        tenant_id=tenant_id, opportunity_id=opportunity_id, contract_id=contract_id,
        asset_id=opp.property_interest[0] if opp.property_interest else None,
        person_id=opp.person_id, owner_id=opp.owner_id, status="OPEN",
        gross_value=gross_value, currency=currency,
        financial_snapshot={"created_from_opportunity": str(opportunity_id)},
        participants=[{"person_id": str(opp.person_id), "role": "buyer"}],
    )
    session.add(deal)
    await session.flush()
    await emit(
        session, event_name="deal.created", tenant_id=tenant_id, aggregate_type="deal",
        aggregate_id=deal.id, payload={"deal_id": str(deal.id)},
    )
    return deal


async def close_deal(
    session: AsyncSession, *, deal: Deal, event: str, actor_id=None,
    reason: str | None = None,
) -> Deal:
    if event not in ("win", "lose", "cancel", "contract"):
        raise ValidationFailed(f"Unknown deal event: {event}")
    before = deal.status
    deal.status = {"win": "WON", "lose": "LOST", "cancel": "CANCELLED", "contract": "CONTRACTED"}[event]
    if deal.status in ("WON", "LOST", "CANCELLED"):
        deal.closed_at = datetime.now(UTC)
        deal.lost_reason = reason if deal.status in ("LOST", "CANCELLED") else None
    await audit(
        session, tenant_id=deal.tenant_id, actor_type="user", actor_id=actor_id,
        action="deal.closed" if deal.status == "WON" else "deal.updated",
        entity_type="deal", entity_id=deal.id, before={"status": before},
        after={"status": deal.status},
    )
    await emit(
        session, event_name="deal.closed" if deal.status == "WON" else "deal.updated",
        tenant_id=deal.tenant_id, aggregate_type="deal", aggregate_id=deal.id,
        payload={"deal_id": str(deal.id), "from": before, "to": deal.status},
    )
    return deal


async def schedule_payment_plan(
    session: AsyncSession, *, tenant_id: uuid.UUID, deal_id: uuid.UUID | None,
    contract_id: uuid.UUID | None, total_amount: Decimal, currency: str,
    down_payment_pct: Decimal | None, installment_count: int,
    installment_period_months: int, first_due: datetime | None = None,
) -> list[PaymentSchedule]:
    """Generate the installment schedule from a payment plan (§37)."""
    total = Decimal(str(total_amount))
    down = (total * down_payment_pct / 100) if down_payment_pct else Decimal("0")
    remaining = total - down
    per_installment = (remaining / installment_count).quantize(Decimal("0.0001"))
    rows: list[PaymentSchedule] = []
    base = first_due or datetime.now(UTC)
    if down > 0:
        rows.append(PaymentSchedule(
            tenant_id=tenant_id, deal_id=deal_id, contract_id=contract_id,
            installment_no=0, amount=down, currency=currency,
            due_at=base, status="scheduled",
        ))
    for i in range(1, installment_count + 1):
        rows.append(PaymentSchedule(
            tenant_id=tenant_id, deal_id=deal_id, contract_id=contract_id,
            installment_no=i, amount=per_installment, currency=currency,
            due_at=base + __import__("datetime").timedelta(days=30 * installment_period_months * i),
            status="scheduled",
        ))
    session.add_all(rows)
    await session.flush()
    for row in rows:
        await emit(
            session, event_name="payment.scheduled", tenant_id=tenant_id,
            aggregate_type="deal", aggregate_id=deal_id,
            payload={"schedule_id": str(row.id), "installment_no": row.installment_no,
                     "amount": str(row.amount), "currency": row.currency},
        )
    return rows


async def record_payment(
    session: AsyncSession, *, tenant_id: uuid.UUID, deal_id: uuid.UUID | None,
    amount: Decimal | str, currency: str = "EGP", kind: str = "installment",
    schedule_id: uuid.UUID | None = None, method: str | None = None,
    actor_id=None,
) -> Payment:
    payment = Payment(
        tenant_id=tenant_id, deal_id=deal_id, amount=Decimal(str(amount)),
        currency=currency, kind=kind, status="completed", paid_at=datetime.now(UTC),
        method=method,
    )
    session.add(payment)
    await session.flush()
    if schedule_id:
        row = await session.get(PaymentSchedule, schedule_id)
        if row is None:
            raise NotFound("Schedule row not found")
        row.status = "completed"
        row.payment_id = payment.id
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="payment.recorded", entity_type="payment", entity_id=payment.id,
        after={"amount": str(payment.amount), "kind": kind},
    )
    await emit(
        session, event_name="payment.completed", tenant_id=tenant_id,
        aggregate_type="deal", aggregate_id=deal_id,
        payload={"payment_id": str(payment.id), "amount": str(payment.amount),
                 "currency": currency, "kind": kind, "schedule_id": str(schedule_id) if schedule_id else None},
    )
    return payment


async def calculate_commissions(
    session: AsyncSession, *, tenant_id: uuid.UUID, deal_id: uuid.UUID,
    actor_id=None,
) -> list[Commission]:
    """Commission engine (§38): deal state + approved rules → commission rows."""
    deal = (
        await session.execute(select(Deal).where(Deal.id == deal_id, Deal.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if deal is None:
        raise NotFound("Deal not found")
    if deal.status != "WON":
        raise Conflict("Commissions are calculated on WON deals only",
                       details={"deal_status": deal.status})
    basis_amount = deal.gross_value or Decimal("0")
    rules = (
        await session.execute(
            select(CommissionRule).where(
                CommissionRule.tenant_id == tenant_id, CommissionRule.is_active.is_(True)
            )
        )
    ).scalars().all()
    if not rules:
        raise ValidationFailed("No active commission rules configured")
    rule = rules[0]  # scope matching (project/branch) refines selection as data grows
    created: list[Commission] = []
    for beneficiary_type, pct in rule.splits.items():
        if not pct:
            continue
        amount = (basis_amount * Decimal(str(pct)) / Decimal("100")).quantize(Decimal("0.0001"))
        beneficiary_id = deal.owner_id if beneficiary_type == "sales_rep" else None
        commission = Commission(
            tenant_id=tenant_id, deal_id=deal_id, rule_id=rule.id,
            beneficiary_type=beneficiary_type,
            beneficiary_id=str(beneficiary_id) if beneficiary_id else None,
            basis_amount=basis_amount, rate=Decimal(str(pct)), amount=amount,
            currency=deal.currency, status="calculated",
        )
        session.add(commission)
        created.append(commission)
    await session.flush()
    await emit(
        session, event_name="commission.created", tenant_id=tenant_id,
        aggregate_type="deal", aggregate_id=deal_id,
        payload={"deal_id": str(deal_id), "commissions": [str(c.id) for c in created]},
    )
    return created


async def approve_commission(
    session: AsyncSession, *, commission: Commission, actor_id,
) -> Commission:
    commission.status = "approved"
    commission.approved_by = actor_id
    commission.approved_at = datetime.now(UTC)
    await audit(
        session, tenant_id=commission.tenant_id, actor_type="user", actor_id=actor_id,
        action="commission.approved", entity_type="commission", entity_id=commission.id,
        after={"amount": str(commission.amount)},
    )
    return commission


async def write_commission_splits(
    session: AsyncSession, *, tenant_id: uuid.UUID, deal_id: uuid.UUID,
    splits: list[dict[str, Any]], actor_id=None,
) -> list[CommissionSplit]:
    """V4 1.29: financial splits enforced by the DB trigger —
    SUM(share_percentage) per deal MUST equal 100.00 or the tx ROLLBACKS."""
    from app.decision.models import CommissionSplit

    if not splits:
        raise ValidationFailed("splits required")
    # replace existing splits for the deal (idempotent re-write)
    existing = (
        await session.execute(
            select(CommissionSplit).where(CommissionSplit.deal_id == deal_id)
        )
    ).scalars().all()
    for row in existing:
        await session.delete(row)
    await session.flush()
    created = []
    for s in splits:
        row = CommissionSplit(
            tenant_id=tenant_id, deal_id=deal_id,
            party_role=s["party_role"], party_id=s.get("party_id"),
            share_percentage=Decimal(str(s["share_percentage"])),
        )
        session.add(row)
        created.append(row)
    await session.flush()
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="commission.splits_written", entity_type="deal", entity_id=deal_id,
        after={"splits": splits},
    )
    return created
