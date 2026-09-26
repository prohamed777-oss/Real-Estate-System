"""Analytics API: snapshots, funnel, NL queries."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import execute_query_plan, funnel, operational_snapshot, parse_nl_question
from app.core.db import get_session
from app.core.permissions import ANALYTICS_READ, require
from app.core.tenancy import AuthContext

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
async def overview(
    auth: AuthContext = Depends(require(ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await operational_snapshot(session, auth.tenant_id)


@router.get("/funnel")
async def get_funnel(
    auth: AuthContext = Depends(require(ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await funnel(session, auth.tenant_id)


class NLQueryIn(BaseModel):
    question: str


@router.post("/query")
async def nl_query(
    body: NLQueryIn,
    auth: AuthContext = Depends(require(ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Natural language → deterministic structured plan → data service →
    result (§73). The LLM NEVER generates SQL — see ai.analytics agent for the
    explanation layer."""
    plan = parse_nl_question(body.question)
    result = await execute_query_plan(session, auth.tenant_id, plan)
    return result
