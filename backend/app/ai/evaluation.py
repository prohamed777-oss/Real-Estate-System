"""AI Evaluation suite (§63): regression gate for any new agent/prompt version.

Metrics: intent accuracy, tool selection accuracy, response quality (contains),
escalation correctness, latency. Runs against the live provider (or mock in
tests) — results stored in eval_runs as the version gate.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import EvalRun
from app.ai.runtime import execute_agent, get_profile


async def run_evaluation(
    session: AsyncSession, *, tenant_id: uuid.UUID, agent_key: str,
    cases: list[dict[str, Any]], dataset_name: str = "adhoc",
) -> EvalRun:
    """Case shape:
        {"input": str,
         "expected_tools": ["search_properties"],   # optional
         "must_contain": ["متاح"],                   # optional substring(s)
         "must_not_contain": ["أضمن"],               # optional
         "expect_escalation": bool}                  # optional
    """
    profile = await get_profile(session, tenant_id, agent_key)
    results = []
    tool_hits = response_hits = 0
    started = time.monotonic()

    for case in cases:
        result = await execute_agent(
            session, tenant_id=tenant_id, profile=profile,
            user_message=case["input"], permissions=set(profile.permissions or []),
        )
        called_tools = {tc["name"] for tc in result.tool_calls}
        expected_tools = set(case.get("expected_tools") or [])
        tool_ok = expected_tools.issubset(called_tools) if expected_tools else True
        text = result.message or ""
        contains_ok = all(s in text for s in case.get("must_contain", []))
        not_contains_ok = all(s not in text for s in case.get("must_not_contain", []))
        escalation_ok = (
            result.needs_human == case.get("expect_escalation", False)
            if "expect_escalation" in case else True
        )
        passed = tool_ok and contains_ok and not_contains_ok and escalation_ok
        tool_hits += int(tool_ok)
        response_hits += int(contains_ok and not_contains_ok)
        results.append({
            "input": case["input"][:200], "passed": passed,
            "tool_ok": tool_ok, "response_ok": contains_ok and not_contains_ok,
            "escalation_ok": escalation_ok,
            "tools_called": sorted(called_tools),
            "status": result.status,
        })

    total = len(cases) or 1
    run = EvalRun(
        tenant_id=tenant_id, agent_key=agent_key, agent_version=profile.version,
        dataset_name=dataset_name,
        metrics={
            "cases": len(cases),
            "passed": sum(1 for r in results if r["passed"]),
            "tool_selection_accuracy": round(tool_hits / total, 4),
            "response_quality": round(response_hits / total, 4),
            "latency_ms": int((time.monotonic() - started) * 1000),
        },
        cases=results,
    )
    session.add(run)
    await session.flush()
    return run
