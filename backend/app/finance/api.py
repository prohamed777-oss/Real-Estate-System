"""Finance API: deals, contracts, payments, commissions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFound
from app.core.permissions import DEALS_READ, DEALS_WRITE, FINANCE_READ, FINANCE_WRITE, require
from app.core.tenancy import AuthContext
from app.finance.models import Commission, CommissionRule, Contract, Deal, Payment, PaymentSchedule
from app.finance.service import (
    approve_commission,
    calculate_commissions,
    close_deal,
    create_contract_from_reservation,
    create_deal,
    record_payment,
    schedule_payment_plan,
)

router = APIRouter(tags=["finance"])


class DealIn(BaseModel):
    opportunity_id: uuid.UUID
    contract_id: uuid.UUID | None = None
    gross_value: Decimal | None = None
    currency: str = "EGP"


class DealCloseIn(BaseModel):
    event: str
    reason: str | None = None


class ContractFromReservationIn(BaseModel):
    reservation_id: uuid.UUID


class ScheduleIn(BaseModel):
    deal_id: uuid.UUID | None = None
    contract_id: uuid.UUID | None = None
    total_amount: Decimal
    currency: str = "EGP"
    down_payment_pct: Decimal | None = None
    installment_count: int
    installment_period_months: int = 1


class PaymentIn(BaseModel):
    deal_id: uuid.UUID | None = None
    amount: Decimal
    currency: str = "EGP"
    kind: str = "installment"
    schedule_id: uuid.UUID | None = None
    method: str | None = None


class CommissionRuleIn(BaseModel):
    name: str
    scope: dict[str, Any] = {}
    basis: str = "deal_value"
    splits: dict[str, Any]
    valid_from: str | None = None
    valid_to: str | None = None


@router.post("/contracts/from-reservation", status_code=201)
async def post_contract(
    body: ContractFromReservationIn,
    auth: AuthContext = Depends(require(FINANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    contract = await create_contract_from_reservation(
        session, tenant_id=auth.tenant_id, reservation_id=body.reservation_id,
        actor_id=auth.user_id,
    )
    return {"id": str(contract.id), "status": contract.status}


@router.post("/deals", status_code=201)
async def post_deal(
    body: DealIn,
    auth: AuthContext = Depends(require(DEALS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    deal = await create_deal(
        session, tenant_id=auth.tenant_id, opportunity_id=body.opportunity_id,
        contract_id=body.contract_id, gross_value=body.gross_value,
        currency=body.currency, actor_id=auth.user_id,
    )
    return {"id": str(deal.id), "status": deal.status}


@router.get("/deals")
async def list_deals(
    auth: AuthContext = Depends(require(DEALS_READ)),
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = select(Deal).where(Deal.tenant_id == auth.tenant_id)
    if status:
        query = query.where(Deal.status == status)
    rows = (await session.execute(query.order_by(Deal.created_at.desc()).limit(100))).scalars().all()
    return [
        {
            "id": str(d.id), "opportunity_id": str(d.opportunity_id),
            "asset_id": str(d.asset_id) if d.asset_id else None,
            "status": d.status, "gross_value": str(d.gross_value) if d.gross_value else None,
            "currency": d.currency, "closed_at": d.closed_at.isoformat() if d.closed_at else None,
        }
        for d in rows
    ]


@router.post("/deals/{deal_id}/close")
async def do_close_deal(
    deal_id: uuid.UUID,
    body: DealCloseIn,
    auth: AuthContext = Depends(require(DEALS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    deal = (
        await session.execute(select(Deal).where(Deal.id == deal_id, Deal.tenant_id == auth.tenant_id))
    ).scalar_one_or_none()
    if deal is None:
        raise NotFound("Deal not found")
    await close_deal(session, deal=deal, event=body.event, actor_id=auth.user_id,
                     reason=body.reason)
    return {"id": str(deal.id), "status": deal.status}


@router.post("/payment-schedules", status_code=201)
async def post_schedule(
    body: ScheduleIn,
    auth: AuthContext = Depends(require(FINANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await schedule_payment_plan(
        session, tenant_id=auth.tenant_id, deal_id=body.deal_id,
        contract_id=body.contract_id, total_amount=body.total_amount,
        currency=body.currency, down_payment_pct=body.down_payment_pct,
        installment_count=body.installment_count,
        installment_period_months=body.installment_period_months,
    )
    return {"rows": len(rows), "total_scheduled": str(sum(r.amount for r in rows))}


@router.post("/payments", status_code=201)
async def post_payment(
    body: PaymentIn,
    auth: AuthContext = Depends(require(FINANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    payment = await record_payment(
        session, tenant_id=auth.tenant_id, deal_id=body.deal_id, amount=body.amount,
        currency=body.currency, kind=body.kind, schedule_id=body.schedule_id,
        method=body.method, actor_id=auth.user_id,
    )
    return {"id": str(payment.id), "status": payment.status, "amount": str(payment.amount)}


@router.post("/commission-rules", status_code=201)
async def post_commission_rule(
    body: CommissionRuleIn,
    auth: AuthContext = Depends(require(FINANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rule = CommissionRule(
        tenant_id=auth.tenant_id, name=body.name, scope=body.scope, basis=body.basis,
        splits=body.splits, is_active=True, approved_by=auth.user_id,
    )
    session.add(rule)
    await session.flush()
    return {"id": str(rule.id), "name": rule.name}


@router.post("/deals/{deal_id}/commissions")
async def post_calculate_commissions(
    deal_id: uuid.UUID,
    auth: AuthContext = Depends(require(FINANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = await calculate_commissions(session, tenant_id=auth.tenant_id, deal_id=deal_id,
                                       actor_id=auth.user_id)
    return [
        {"id": str(c.id), "beneficiary_type": c.beneficiary_type,
         "amount": str(c.amount), "rate": str(c.rate), "status": c.status}
        for c in rows
    ]


@router.post("/commissions/{commission_id}/approve")
async def do_approve_commission(
    commission_id: uuid.UUID,
    auth: AuthContext = Depends(require(FINANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    commission = (
        await session.execute(
            select(Commission).where(
                Commission.id == commission_id, Commission.tenant_id == auth.tenant_id
            )
        )
    ).scalar_one_or_none()
    if commission is None:
        raise NotFound("Commission not found")
    await approve_commission(session, commission=commission, actor_id=auth.user_id)
    return {"id": str(commission.id), "status": commission.status}
