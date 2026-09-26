"""Analytics read models + NL query planner (§70-73, §106).

Analytics reads from indexed read models, never blocks transaction tables.
The NL agent NEVER generates SQL (§73): deterministic intent parsing produces a
STRUCTURED query plan; the data service executes it; the LLM only explains.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import Conversation, Message, Task
from app.finance.models import Commission, Deal, Payment
from app.leads.models import Lead, LeadRequirement
from app.properties.models import PropertyAsset, UnitInventory
from app.sales.models import Offer, Opportunity, Reservation, Viewing


async def operational_snapshot(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Operational analytics (§70): counts that matter right now."""
    active_leads = (
        await session.execute(
            select(func.count()).select_from(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.lifecycle_stage.notin_(("CONVERTED", "DISQUALIFIED", "LOST")),
            )
        )
    ).scalar_one()
    unassigned = (
        await session.execute(
            select(func.count()).select_from(Lead).where(
                Lead.tenant_id == tenant_id, Lead.owner_id.is_(None),
                Lead.lifecycle_stage.notin_(("CONVERTED", "DISQUALIFIED")),
            )
        )
    ).scalar_one()
    upcoming_viewings = (
        await session.execute(
            select(func.count()).select_from(Viewing).where(
                Viewing.tenant_id == tenant_id,
                Viewing.status.in_(("REQUESTED", "CONFIRMED")),
                Viewing.scheduled_at >= datetime.now(UTC),
            )
        )
    ).scalar_one()
    open_tasks = (
        await session.execute(
            select(func.count()).select_from(Task).where(
                Task.tenant_id == tenant_id, Task.status == "open"
            )
        )
    ).scalar_one()
    available_units = (
        await session.execute(
            select(func.count()).select_from(UnitInventory).where(
                UnitInventory.tenant_id == tenant_id, UnitInventory.state == "AVAILABLE"
            )
        )
    ).scalar_one()
    reserved_units = (
        await session.execute(
            select(func.count()).select_from(UnitInventory).where(
                UnitInventory.tenant_id == tenant_id, UnitInventory.state == "RESERVED"
            )
        )
    ).scalar_one()
    contracted_units = (
        await session.execute(
            select(func.count()).select_from(UnitInventory).where(
                UnitInventory.tenant_id == tenant_id, UnitInventory.state == "CONTRACTED"
            )
        )
    ).scalar_one()
    return {
        "active_leads": active_leads, "unassigned_leads": unassigned,
        "upcoming_viewings": upcoming_viewings, "open_tasks": open_tasks,
        "available_units": available_units, "reserved_units": reserved_units,
        "contracted_units": contracted_units,
    }


async def funnel(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Revenue funnel (§71): Campaign → Lead → Qualified → Viewing → Offer → Reservation → Deal."""
    leads = (
        await session.execute(select(func.count()).select_from(Lead).where(Lead.tenant_id == tenant_id))
    ).scalar_one()
    qualified = (
        await session.execute(
            select(func.count()).select_from(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.lifecycle_stage.in_(("QUALIFIED", "NURTURE", "CONVERTED")),
            )
        )
    ).scalar_one()
    opportunities = (
        await session.execute(
            select(func.count()).select_from(Opportunity).where(Opportunity.tenant_id == tenant_id)
        )
    ).scalar_one()
    viewings = (
        await session.execute(
            select(func.count()).select_from(Viewing).where(
                Viewing.tenant_id == tenant_id, Viewing.status.in_(("ATTENDED", "COMPLETED"))
            )
        )
    ).scalar_one()
    offers = (
        await session.execute(
            select(func.count()).select_from(Offer).where(Offer.tenant_id == tenant_id)
        )
    ).scalar_one()
    reservations = (
        await session.execute(
            select(func.count()).select_from(Reservation).where(Reservation.tenant_id == tenant_id)
        )
    ).scalar_one()
    deals_won = (
        await session.execute(
            select(func.count()).select_from(Deal).where(
                Deal.tenant_id == tenant_id, Deal.status == "WON"
            )
        )
    ).scalar_one()
    revenue = (
        await session.execute(
            select(func.coalesce(func.sum(Deal.gross_value), 0)).where(
                Deal.tenant_id == tenant_id, Deal.status == "WON"
            )
        )
    ).scalar_one()
    pipeline_value = (
        await session.execute(
            select(func.coalesce(func.sum(Opportunity.estimated_value), 0)).where(
                Opportunity.tenant_id == tenant_id,
                Opportunity.stage.notin_(("WON", "LOST")),
            )
        )
    ).scalar_one()
    commissions = (
        await session.execute(
            select(func.coalesce(func.sum(Commission.amount), 0)).where(
                Commission.tenant_id == tenant_id, Commission.status.in_(("approved", "paid"))
            )
        )
    ).scalar_one()

    def _rate(n: int, d: int) -> float | None:
        return round(n / d, 4) if d else None

    return {
        "leads": leads, "qualified": qualified, "opportunities": opportunities,
        "viewings_attended": viewings, "offers": offers, "reservations": reservations,
        "deals_won": deals_won,
        "revenue": float(revenue or 0), "pipeline_value": float(pipeline_value or 0),
        "commissions_approved": float(commissions or 0),
        "conversion_rates": {
            "lead_to_qualified": _rate(qualified, leads),
            "qualified_to_viewing": _rate(viewings, qualified),
            "viewing_to_offer": _rate(offers, viewings),
            "offer_to_reservation": _rate(reservations, offers),
            "reservation_to_deal": _rate(deals_won, reservations),
        },
    }


# ---------- Natural language analytics (§73): plan-based, no SQL from LLM ----------
INTENT_PATTERNS: list[dict[str, Any]] = [
    {"key": "budget_filter", "patterns": ["ميزانيته", "ميزانية", "budget", "فوق", "أقل من", "under", "over"],
     "extract": "budget"},
    {"key": "bedrooms_filter", "patterns": ["غرف", "غرفة", "bedrooms", "bedroom"], "extract": "bedrooms"},
    {"key": "no_contact", "patterns": ["مححدش كلمه", "محدش كلم", "no contact", "لم يتم التواصل", "كلمه"], "extract": "no_contact_days"},
    {"key": "area_filter", "patterns": ["منطقة", "في", "منطقه", "area"], "extract": "area"},
    {"key": "stage_filter", "patterns": ["مؤهل", "qualified", "جديد", "new", "خامل", "dormant"], "extract": "stage"},
]


def parse_nl_question(question: str) -> dict[str, Any]:
    """Deterministic Arabic/English intent parsing → structured plan."""
    q = question.lower()
    plan: dict[str, Any] = {"entity": "leads", "filters": {}, "explanation_parts": []}

    # budget: "فوق 6 مليون" / "over 6m" / "budget 8m"
    import re

    m = re.search(r"(?:فوق|أكتر من|over|more than|>)\s*(\d+(?:[.,]\d+)?)\s*(مليون|million|m)?", q)
    if m:
        value = float(m.group(1).replace(",", ""))
        if m.group(2):
            value *= 1_000_000
        plan["filters"]["max_budget"] = value
        plan["explanation_parts"].append(f"budget >= {value:,.0f}")
    m = re.search(r"(?:أقل من|under|less than|<)\s*(\d+(?:[.,]\d+)?)\s*(مليون|million|m)?", q)
    if m:
        value = float(m.group(1).replace(",", ""))
        if m.group(2):
            value *= 1_000_000
        plan["filters"]["budget_below"] = value
        plan["explanation_parts"].append(f"budget < {value:,.0f}")

    m = re.search(r"(\d+)\s*(?:غرف|غرفة|bedrooms?)", q)
    if m:
        plan["filters"]["bedrooms"] = int(m.group(1))
        plan["explanation_parts"].append(f"bedrooms = {m.group(1)}")

    m = re.search(r"(\d+)\s*(?:يوم|أيام|days?)", q)
    week_match = re.search(r"(?:من|since)\s*أسبوع|a week|one week", q)
    month_match = re.search(r"(?:من|since)\s*شهر|a month|one month", q)
    no_contact = any(p in q for p in ["محدش كلم", "مححدش كلم", "كلمهم", "لم يتم التواصل",
                                       "no contact", "not contacted"])
    if m or week_match or month_match or no_contact:
        if month_match:
            days = 30
        elif week_match:
            days = 7
        elif m:
            days = int(m.group(1))
        else:
            days = 7
        plan["filters"]["no_contact_days"] = days
        plan["explanation_parts"].append(f"no contact in last {days} days")

    plan["limit"] = 50
    plan["sort"] = "-lead_score"
    return plan


async def execute_query_plan(session: AsyncSession, tenant_id: uuid.UUID,
                             plan: dict[str, Any]) -> dict[str, Any]:
    """Data service executes the structured plan (authorization-aware by tenant)."""
    filters = plan.get("filters", {})
    query = select(Lead, LeadRequirement).join(
        LeadRequirement, LeadRequirement.lead_id == Lead.id, isouter=True
    ).where(
        Lead.tenant_id == tenant_id,
        Lead.lifecycle_stage.notin_(("CONVERTED", "DISQUALIFIED")),
    )
    if "max_budget" in filters:
        query = query.where(
            func.cast(func.coalesce(LeadRequirement.explicit.op('->>')('max_budget'), '0'),
                      __import__("sqlalchemy").Numeric) >= filters["max_budget"]
        )
    if "bedrooms" in filters:
        query = query.where(
            LeadRequirement.explicit.op('->>')('bedrooms').astext == str(filters["bedrooms"])
        )
    rows = (await session.execute(query.limit(plan.get("limit", 50)))).all()

    out = []
    now = datetime.now(UTC)
    for lead, req in rows:
        item = {
            "lead_id": str(lead.id), "stage": lead.lifecycle_stage, "score": lead.lead_score,
            "person_id": str(lead.person_id),
            "budget": (req.explicit or {}).get("max_budget") if req else None,
            "bedrooms": (req.explicit or {}).get("bedrooms") if req else None,
        }
        if "no_contact_days" in filters:
            # last inbound message across the person's conversations
            last_inbound = (
                await session.execute(
                    select(func.max(Message.created_at))
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Conversation.person_id == lead.person_id,
                        Message.direction == "inbound",
                    )
                )
            ).scalar_one_or_none()
            days_silent = (now - last_inbound).days if last_inbound else None
            if days_silent is None or days_silent < filters["no_contact_days"]:
                continue
            item["days_silent"] = days_silent
        out.append(item)
    return {"plan": plan, "count": len(out), "results": out[:20]}
