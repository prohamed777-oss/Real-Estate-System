"""AI API: agent execution, copilots, profiles, execution ledger."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIExecution, AgentProfile
from app.ai.runtime import execute_agent, get_profile
from app.core.db import get_session
from app.core.errors import NotFound
from app.core.permissions import AI_READ, AI_RUN, require
from app.core.tenancy import AuthContext

router = APIRouter(prefix="/ai", tags=["ai"])


class AgentExecuteIn(BaseModel):
    message: str
    conversation_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    person_id: uuid.UUID | None = None
    high_value_approved: bool = False
    history: list[dict[str, Any]] | None = None


@router.post("/agents/{key}/execute")
async def execute(
    key: str,
    body: AgentExecuteIn,
    auth: AuthContext = Depends(require(AI_RUN)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    profile = await get_profile(session, auth.tenant_id, key)
    result = await execute_agent(
        session, tenant_id=auth.tenant_id, profile=profile, user_message=body.message,
        conversation_id=body.conversation_id, lead_id=body.lead_id, person_id=body.person_id,
        permissions=set(auth.permissions), history=body.history,
        high_value_approved=body.high_value_approved,
    )
    return result.to_dict()


@router.get("/agents")
async def list_profiles(
    auth: AuthContext = Depends(require(AI_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(AgentProfile)
            .where(AgentProfile.is_active.is_(True))
            .order_by(AgentProfile.key)
        )
    ).scalars().all()
    return [
        {"key": p.key, "name": p.name, "model_profile": p.model_profile,
         "version": p.version, "max_steps": p.max_steps,
         "tools": p.tool_scopes, "is_template": p.tenant_id is None}
        for p in rows
    ]


@router.get("/executions")
async def list_executions(
    auth: AuthContext = Depends(require(AI_READ)),
    session: AsyncSession = Depends(get_session),
    agent_key: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """AI Action Ledger view (§95)."""
    query = select(AIExecution).where(AIExecution.tenant_id == auth.tenant_id)
    if agent_key:
        query = query.where(AIExecution.agent_key == agent_key)
    rows = (
        await session.execute(query.order_by(AIExecution.started_at.desc()).limit(limit))
    ).scalars().all()
    return [
        {
            "id": str(e.id), "agent_key": e.agent_key, "agent_version": e.agent_version,
            "model": e.model, "status": e.status, "latency_ms": e.latency_ms,
            "tokens": {"in": e.input_tokens, "out": e.output_tokens},
            "cost_usd": e.cost_usd, "tool_calls": e.tool_calls,
            "guardrail_flags": e.guardrail_flags, "human_handoff": e.human_handoff,
            "started_at": e.started_at.isoformat(),
        }
        for e in rows
    ]


class SalesCopilotIn(BaseModel):
    lead_id: uuid.UUID


@router.post("/copilot/sales")
async def sales_copilot(
    body: SalesCopilotIn,
    auth: AuthContext = Depends(require(AI_RUN)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Sales Copilot (§53): context summary + suggested next action."""
    from sqlalchemy import select as sel

    from app.leads.models import Lead, LeadRequirement

    lead = (
        await session.execute(
            sel(Lead).where(Lead.id == body.lead_id, Lead.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if lead is None:
        raise NotFound("Lead not found")
    req = (
        await session.execute(sel(LeadRequirement).where(LeadRequirement.lead_id == lead.id))
    ).scalar_one_or_none()
    profile = await get_profile(session, auth.tenant_id, "sales_copilot")
    context = {
        "lead": {"stage": lead.lifecycle_stage, "score": lead.lead_score,
                  "next_action": lead.next_action},
        "requirements": (req.explicit if req else {}) or {},
        "objections": (req.objections if req else []) or [],
    }
    result = await execute_agent(
        session, tenant_id=auth.tenant_id, profile=profile,
        user_message="لخص حالة العميل واقترح الـnext best action.",
        lead_id=lead.id, person_id=lead.person_id, permissions=set(auth.permissions),
    )
    return {**result.to_dict(), "context": context}
