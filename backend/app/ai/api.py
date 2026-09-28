"""AI API: agent execution, copilots, profiles, execution ledger."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AgentProfile, AIExecution
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
    # V4 5.4: approvals are REAL ApprovalRequest rows bound to args —
    # a boolean can never unlock an approval-required tool.
    approval_id: uuid.UUID | None = None
    history: list[dict[str, Any]] | None = None


@router.post("/agents/{key}/execute")
async def execute(
    key: str,
    body: AgentExecuteIn,
    auth: AuthContext = Depends(require(AI_RUN)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    # §98-100 gates: feature flag → monthly quota → rate limit → metered usage
    from app.analytics.billing import check_quota, record_usage
    from app.core.ratelimit import check_rate_limit
    from app.organizations.models import FeatureFlag

    flag_key = {"reactivation": "ai_reactivation"}.get(key)
    if flag_key:
        flag = (
            await session.execute(
                select(FeatureFlag).where(
                    FeatureFlag.tenant_id == auth.tenant_id, FeatureFlag.key == flag_key
                )
            )
        ).scalar_one_or_none()
        if flag is not None and not flag.enabled:
            from app.core.errors import DomainError

            raise DomainError(
                f"Feature {flag_key} is disabled for this tenant (§100)",
                code="feature_disabled", status_code=403,
            )
    await check_quota(session, tenant_id=auth.tenant_id, kind="ai_requests")
    await check_rate_limit(
        session, key=f"tenant:{auth.tenant_id}:ai", limit=30, window_seconds=60
    )
    profile = await get_profile(session, auth.tenant_id, key)
    result = await execute_agent(
        session, tenant_id=auth.tenant_id, profile=profile, user_message=body.message,
        conversation_id=body.conversation_id, lead_id=body.lead_id, person_id=body.person_id,
        permissions=set(auth.permissions), history=body.history,
        approval_id=body.approval_id,
    )
    await record_usage(session, tenant_id=auth.tenant_id, kind="ai_requests",
                       cost_usd=0.0)
    return result.to_dict()


@router.post("/evals/run")
async def run_eval(
    body: dict[str, Any],
    auth: AuthContext = Depends(require(AI_RUN)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """§63: evaluation suite — gate for any new agent/prompt version."""
    from app.ai.evaluation import run_evaluation

    agent_key = body.get("agent_key")
    cases = body.get("cases") or []
    if not agent_key or not cases:
        from app.core.errors import ValidationFailed

        raise ValidationFailed("agent_key and cases are required")
    run = await run_evaluation(
        session, tenant_id=auth.tenant_id, agent_key=agent_key, cases=cases,
        dataset_name=body.get("dataset_name", "adhoc"),
    )
    return {
        "id": str(run.id), "agent_key": run.agent_key, "agent_version": run.agent_version,
        "metrics": run.metrics,
        "cases": run.cases,
    }


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
    limit: int = Query(50, ge=1, le=200),
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
