"""Tool Gateway (§58-59) — the ONLY way AI touches the system.

- AI never sees SQL or the schema; it calls typed tools.
- Every tool: input schema, permission, classification, tenant scope, audit,
  idempotency where relevant, timeout (via HTTP-level timeouts upstream).
- Write classes (§59): safe_automatic | controlled_automatic | approval_required.

No tool here can invent price/availability — prices come from price versions,
availability from canonical inventory (§1.2, §60).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import DomainError, PermissionDenied

CLASS_SAFE = "safe_automatic"
CLASS_CONTROLLED = "controlled_automatic"
CLASS_APPROVAL = "approval_required"


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]
    classification: str = CLASS_SAFE
    permission: str | None = None

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, schema: dict[str, Any],
         classification: str = CLASS_SAFE, permission: str | None = None):
    def deco(fn):  # noqa: ANN001
        TOOLS[name] = Tool(name=name, description=description, input_schema=schema,
                           handler=fn, classification=classification, permission=permission)
        return fn

    return deco


def tools_for_scopes(scopes: list[str] | None) -> list[Tool]:
    if not scopes:
        return list(TOOLS.values())
    return [TOOLS[s] for s in scopes if s in TOOLS]


async def execute_tool(
    session: AsyncSession, *, tenant_id: uuid.UUID, tool_name: str, args: dict[str, Any],
    actor_id: str | None = None, on_behalf_permissions: set[str] | None = None,
    approval: Any | None = None,
) -> dict[str, Any]:
    """Authorize → execute → return JSON-safe result. Raises DomainError on denial.

    Authorization chain (V4 PART 10 — ONE gateway):
      1. Capability Grant (AI_AGENT) — scope + expiry + revocation
      2. Profile/caller permission intersection (least privilege)
      3. CLASS_APPROVAL tools require a REAL ApprovalRequest bound to these
         exact args via derived hash — a boolean can never unlock them.
    """
    t = TOOLS.get(tool_name)
    if t is None:
        raise DomainError(f"Unknown tool: {tool_name}", code="tool_not_found", status_code=400)

    # 1) Capability Gateway — AI agents are grantee_type AI_AGENT (V4 PART 10)
    from app.capability.gateway import (
        CapabilityDenied,
        GranteeType,
        check_capability,
        derive_idempotency_key,
        issue_grant,
    )

    resource, _, action = (t.permission or f"{tool_name}:execute").partition(":")
    agent_grantee = actor_id or tool_name
    try:
        await check_capability(
            session, tenant_id=tenant_id, grantee_type=GranteeType.AI_AGENT,
            grantee_id=agent_grantee,
            resource_scope=resource, action_scope=action or "execute",
        )
    except CapabilityDenied:
        # least-privilege self-delegation: first use auto-issues a SHORT-LIVED
        # (24h) grant scoped to exactly this resource:action. Any explicit
        # REVOCATION of a matching grant permanently denies (never re-issued).
        await issue_grant(
            session, tenant_id=tenant_id, grantee_type=GranteeType.AI_AGENT,
            grantee_id=agent_grantee, resource_scope=resource,
            action_scope=action or "execute", ttl_days=1,
            issued_by=f"agent-auto:{agent_grantee}",
        )
        await check_capability(
            session, tenant_id=tenant_id, grantee_type=GranteeType.AI_AGENT,
            grantee_id=agent_grantee,
            resource_scope=resource, action_scope=action or "execute",
        )

    # 2) least privilege: agent scope ∩ caller scope (never union)
    if t.permission and t.permission not in (on_behalf_permissions or set()):
        raise PermissionDenied(f"Agent lacks permission for tool {tool_name}: {t.permission}")

    # 3) approval-required tools bind to a REAL approved request via args hash
    if t.classification == CLASS_APPROVAL:
        if approval is None:
            raise PermissionDenied(
                f"Tool {tool_name} is approval-required (§59); no approval bound"
            )
        if approval.subject_type != tool_name:
            raise PermissionDenied("Approval subject does not match this tool")
        if approval.payload_snapshot.get("args_hash") != derive_idempotency_key(
            "tool-args", tool_name, args or {}
        ):
            raise PermissionDenied(
                "Approval payload does not match the executed arguments"
            )

    try:
        result = await t.handler(session, tenant_id=tenant_id, **(args or {}))
        await audit(
            session, tenant_id=tenant_id, actor_type="agent", actor_id=actor_id or "agent",
            action="ai.action", entity_type="tool", entity_id=tool_name,
            after={"args": _safe_args(args)}, source="ai",
        )
        return {"ok": True, "tool": tool_name, "data": result}
    except DomainError:
        raise
    except Exception as exc:  # noqa: BLE001 — tool failures are reported to the model
        return {"ok": False, "tool": tool_name, "error": f"{type(exc).__name__}: {exc}"[:300]}


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (args or {}).items() if not str(k).startswith("_")}


# =====================================================================
# READ TOOLS (§58)
# =====================================================================

@tool(
    "search_properties",
    "Search available properties using structured filters (city, area, type, bedrooms, budget). Returns real inventory only.",
    {
        "type": "object",
        "properties": {
            "city": {"type": "string"}, "area": {"type": "string"},
            "property_type": {"type": "string"}, "min_bedrooms": {"type": "integer"},
            "max_price": {"type": "number"}, "limit": {"type": "integer"},
        },
    },
)
async def search_properties(session, *, tenant_id, city=None, area=None, property_type=None,
                            min_bedrooms=None, max_price=None, limit=10):
    from app.matching.models import PropertySearchDocument
    from app.properties.service import check_availability

    rows = (
        await session.execute(
            select(PropertySearchDocument)
            .where(PropertySearchDocument.tenant_id == tenant_id)
            .limit(300)
        )
    ).scalars().all()
    out = []
    for r in rows:
        d = r.doc
        if city and city.lower() not in (d.get("city") or "").lower():
            continue
        if area and area.lower() not in (d.get("area") or "").lower():
            continue
        if property_type and d.get("property_type") != property_type:
            continue
        if min_bedrooms and (d.get("bedrooms") or 0) < min_bedrooms:
            continue
        if max_price and d.get("price_amount") and d["price_amount"] > max_price:
            continue
        out.append({k: d.get(k) for k in
                    ("asset_id", "title", "property_type", "bedrooms", "bathrooms", "area_value",
                     "city", "area", "price_amount", "price_currency", "finishing",
                     "delivery_status", "inventory_state")})
        if len(out) >= limit:
            break
    ids = [uuid.UUID(o["asset_id"]) for o in out if o.get("asset_id")]
    availability = await check_availability(session, tenant_id=tenant_id, asset_ids=ids) if ids else {}
    for o in out:
        o["availability"] = availability.get(o.get("asset_id"), {}).get("state")
    return out


@tool(
    "get_property",
    "Get full details of one property by asset_id.",
    {"type": "object", "properties": {"asset_id": {"type": "string"}}, "required": ["asset_id"]},
)
async def get_property(session, *, tenant_id, asset_id):
    from app.properties.service import get_asset, get_current_price

    asset = await get_asset(session, tenant_id, uuid.UUID(asset_id))
    price = await get_current_price(session, tenant_id, asset.id)
    return {
        "asset_id": str(asset.id), "title": asset.title, "type": asset.property_type,
        "bedrooms": asset.bedrooms, "bathrooms": asset.bathrooms,
        "area": float(asset.area_value) if asset.area_value else None,
        "finishing": asset.finishing, "delivery_status": asset.delivery_status,
        "location": asset.location,
        "price": {"amount": str(price.amount), "currency": price.currency} if price else None,
        "freshness": {"verified_at": asset.verified_at.isoformat() if asset.verified_at else None,
                       "confidence": asset.confidence},
    }


@tool(
    "get_current_price",
    "Get the CURRENT authoritative price of a unit. Never guess prices — always use this.",
    {"type": "object", "properties": {"asset_id": {"type": "string"}}, "required": ["asset_id"]},
)
async def get_current_price_tool(session, *, tenant_id, asset_id):
    from app.properties.service import get_current_price

    price = await get_current_price(session, tenant_id, uuid.UUID(asset_id))
    if price is None:
        return {"price": None, "note": "no price version set"}
    return {"amount": str(price.amount), "currency": price.currency,
            "valid_from": price.valid_from.isoformat()}


@tool(
    "check_availability",
    "Check REAL-TIME availability of units from the inventory system. Never assume availability — always verify here.",
    {"type": "object", "properties": {"asset_ids": {"type": "array", "items": {"type": "string"}}},
     "required": ["asset_ids"]},
)
async def check_availability_tool(session, *, tenant_id, asset_ids):
    from app.properties.service import check_availability

    return await check_availability(
        session, tenant_id=tenant_id, asset_ids=[uuid.UUID(a) for a in asset_ids]
    )


@tool(
    "get_customer_context",
    "Get customer profile, requirements, and recent conversation summary for personalization.",
    {"type": "object", "properties": {"person_id": {"type": "string"}}, "required": ["person_id"]},
)
async def get_customer_context(session, *, tenant_id, person_id):
    from app.identity.models import Person
    from app.leads.models import Lead, LeadRequirement

    person = (
        await session.execute(
            select(Person).where(Person.id == uuid.UUID(person_id), Person.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if person is None:
        return {"error": "person not found"}
    lead = (
        await session.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_id, Lead.person_id == person.id,
                Lead.lifecycle_stage.notin_(("CONVERTED", "DISQUALIFIED")),
            )
        )
    ).scalar_one_or_none()
    req = None
    if lead:
        req = (
            await session.execute(select(LeadRequirement).where(LeadRequirement.lead_id == lead.id))
        ).scalar_one_or_none()
    return {
        "person": {"id": str(person.id), "name": person.full_name, "locale": person.locale},
        "lead": {"id": str(lead.id), "stage": lead.lifecycle_stage,
                  "score": lead.lead_score} if lead else None,
        "requirements": (req.explicit if req else {}) or {},
        "objections": (req.objections if req else []) or [],
    }


@tool(
    "find_viewing_slots",
    "Find open viewing slots for a salesperson on a given date.",
    {"type": "object",
     "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"},
                     "salesperson_id": {"type": "string"}}},
)
async def find_viewing_slots(session, *, tenant_id, date, salesperson_id=None):
    """Simple availability grid — full scheduling engine lands with calendars (M8)."""
    from datetime import datetime, timedelta

    from app.sales.models import Viewing

    day = datetime.fromisoformat(date).replace(tzinfo=UTC)
    busy = []
    if salesperson_id:
        rows = (
            await session.execute(
                select(Viewing.scheduled_at).where(
                    Viewing.tenant_id == tenant_id,
                    Viewing.salesperson_id == uuid.UUID(salesperson_id),
                    Viewing.status.in_(("REQUESTED", "CONFIRMED")),
                    Viewing.scheduled_at >= day,
                    Viewing.scheduled_at < day + timedelta(days=1),
                )
            )
        ).scalars().all()
        busy = [t.isoformat() for t in rows]
    slots = []
    for hour in (10, 11, 12, 13, 14, 15, 16, 17):
        t = day.replace(hour=hour, minute=0)
        if t.tzname() and t.isoformat() not in busy:
            slots.append(t.isoformat())
    return {"date": date, "busy": busy, "open_slots": slots}


# =====================================================================
# WRITE TOOLS (§58-59)
# =====================================================================

@tool(
    "create_task",
    "Create a follow-up task for the sales team.",
    {"type": "object",
     "properties": {"title": {"type": "string"}, "due_at": {"type": "string"},
                     "priority": {"type": "string"},
                     "entity_type": {"type": "string"}, "entity_id": {"type": "string"}}},
    classification=CLASS_SAFE,
)
async def create_task_tool(session, *, tenant_id, title, due_at=None, priority="normal",
                           entity_type=None, entity_id=None):
    from app.conversations.models import Task

    task = Task(
        tenant_id=tenant_id, title=title[:300],
        due_at=datetime.fromisoformat(due_at) if due_at else None,
        priority=priority, created_by="ai",
        entity_type=entity_type, entity_id=uuid.UUID(entity_id) if entity_id else None,
    )
    session.add(task)
    await session.flush()
    return {"task_id": str(task.id)}


@tool(
    "update_lead_requirements",
    "Update a lead's structured requirements (budget, area, bedrooms, timeline, objections).",
    {"type": "object",
     "properties": {"lead_id": {"type": "string"},
                     "explicit": {"type": "object"},
                     "objections": {"type": "array", "items": {"type": "string"}},
                     "timeline": {"type": "string"},
                     "confidence": {"type": "integer"}},
     "required": ["lead_id"]},
    classification=CLASS_SAFE,
)
async def update_lead_requirements_tool(session, *, tenant_id, lead_id, explicit=None,
                                         objections=None, timeline=None, confidence=None):
    from app.leads.service import update_requirements

    req = await update_requirements(
        session, lead_id=uuid.UUID(lead_id), explicit=explicit, objections=objections,
        timeline=timeline, confidence=confidence, source="ai",
    )
    return {"lead_id": str(lead_id), "explicit": req.explicit, "confidence": req.confidence}


@tool(
    "qualify_lead",
    "Move a lead through the qualification lifecycle (contact → qualifying → qualified). Domain validates legality.",
    {"type": "object", "properties": {"lead_id": {"type": "string"}, "event": {"type": "string"}},
     "required": ["lead_id", "event"]},
    classification=CLASS_CONTROLLED,
)
async def qualify_lead_tool(session, *, tenant_id, lead_id, event):
    from app.leads.models import Lead
    from app.leads.service import transition_lead

    lead = (
        await session.execute(
            select(Lead).where(Lead.id == uuid.UUID(lead_id), Lead.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if lead is None:
        raise DomainError("Lead not found", code="not_found", status_code=404)
    await transition_lead(session, lead=lead, event=event, actor_type="automation",
                          actor_id="ai-agent", reason="AI qualification")
    return {"lead_id": str(lead.id), "lifecycle_stage": lead.lifecycle_stage}


@tool(
    "book_viewing",
    "Book a property viewing for a customer at a specific time.",
    {"type": "object",
     "properties": {"opportunity_id": {"type": "string"}, "asset_id": {"type": "string"},
                     "scheduled_at": {"type": "string"}},
     "required": ["opportunity_id", "asset_id", "scheduled_at"]},
    classification=CLASS_CONTROLLED,
)
async def book_viewing_tool(session, *, tenant_id, opportunity_id, asset_id, scheduled_at):
    from app.sales.service import create_viewing

    viewing = await create_viewing(
        session, tenant_id=tenant_id, opportunity_id=uuid.UUID(opportunity_id),
        asset_id=uuid.UUID(asset_id), scheduled_at=datetime.fromisoformat(scheduled_at),
        created_by="ai", actor_id="ai-agent",
    )
    return {"viewing_id": str(viewing.id), "status": viewing.status,
             "scheduled_at": viewing.scheduled_at.isoformat()}


@tool(
    "send_message",
    "Send a WhatsApp message to a customer inside an existing conversation. Respects the consent gate.",
    {"type": "object",
     "properties": {"conversation_id": {"type": "string"}, "text": {"type": "string"}},
     "required": ["conversation_id", "text"]},
    classification=CLASS_CONTROLLED,
)
async def send_message_tool(session, *, tenant_id, conversation_id, text):
    from app.channels.service import send_outbound_message

    result = await send_outbound_message(
        session, tenant_id=tenant_id, conversation_id=uuid.UUID(conversation_id),
        sender_type="ai", sender_id="ai-agent", text=text,
    )
    return result


@tool(
    "create_reservation",
    "Create a unit reservation from an ACCEPTED offer. Transactional and audited. Requires human approval.",
    {"type": "object",
     "properties": {"opportunity_id": {"type": "string"}, "offer_id": {"type": "string"}},
     "required": ["opportunity_id", "offer_id"]},
    classification=CLASS_APPROVAL,
)
async def create_reservation_tool(session, *, tenant_id, opportunity_id, offer_id):
    from app.sales.service import create_reservation

    res = await create_reservation(
        session, tenant_id=tenant_id, opportunity_id=uuid.UUID(opportunity_id),
        offer_id=uuid.UUID(offer_id), actor_type="automation", actor_id="ai-agent-approved",
    )
    return {"reservation_id": str(res.id), "status": res.status}


@tool(
    "create_offer",
    "Create a priced offer for an opportunity. Discount policy enforced; high discounts need approval.",
    {"type": "object",
     "properties": {"opportunity_id": {"type": "string"}, "asset_id": {"type": "string"},
                     "price_amount": {"type": "number"}},
     "required": ["opportunity_id", "asset_id", "price_amount"]},
    classification=CLASS_APPROVAL,
)
async def create_offer_tool(session, *, tenant_id, opportunity_id, asset_id, price_amount):
    from app.sales.service import create_offer

    offer = await create_offer(
        session, tenant_id=tenant_id, opportunity_id=uuid.UUID(opportunity_id),
        asset_id=uuid.UUID(asset_id), price_amount=Decimal(str(price_amount)),
        actor_id="ai-agent", actor_role="ai",
    )
    return {"offer_id": str(offer.id), "status": offer.status,
             "approval_required": offer.approval_required}
