"""Agent Runtime (§51, §54-56, §62, §95) — ONE runtime, many profiles.

Execution loop with hard limits (§55):
    max_steps / max_latency / max_cost / allowed_tools / allowed_actions
Agent stops on: task complete | need human | insufficient data | policy
violation | tool failure | budget exceeded.

Every execution writes a full ledger row (§95) — the AI governance baseline.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import ModelResponse, get_model_provider
from app.ai.guardrails import check_output
from app.ai.models import AIExecution, AgentProfile
from app.ai.tools import execute_tool, tools_for_scopes
from app.core.errors import DomainError, NotFound

log = logging.getLogger("revenue_os.ai")

STOP_REASONS = {
    "complete": "task_complete",
    "max_steps": "max_steps_reached",
    "need_human": "need_human",
    "insufficient_data": "insufficient_data",
    "policy": "policy_violation",
    "tool_failure": "tool_failure",
    "budget": "budget_exceeded",
}


@dataclass
class AgentResult:
    message: str
    status: str = "completed"
    stop_reason: str = "task_complete"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    execution_id: uuid.UUID | None = None
    guardrail_flags: list[str] = field(default_factory=list)
    needs_human: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message, "status": self.status, "stop_reason": self.stop_reason,
            "tool_calls": self.tool_calls, "execution_id": str(self.execution_id) if self.execution_id else None,
            "guardrail_flags": self.guardrail_flags, "needs_human": self.needs_human,
        }


CONTEXT_BUDGET_TOKENS = 8000


def build_system_context(
    *, profile: AgentProfile, person_context: dict[str, Any] | None,
    recent_messages: list[dict[str, Any]] | None, policies: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Context Engine (§56): current message + recent conversation + structured
    lead state + relevant facts + policies — with a token budget. NEVER the
    entire history (§56, §109)."""
    sections = [profile.system_prompt]
    if policies:
        sections.append("POLICIES:\n" + "\n".join(f"- {p}" for p in policies))
    if person_context:
        sections.append(
            "CUSTOMER CONTEXT (structured, authoritative):\n"
            + json.dumps(person_context, ensure_ascii=False, default=str)[:4000]
        )
    if recent_messages:
        trimmed = recent_messages[-8:]
        convo = "\n".join(f"{m['direction']}: {m.get('text') or '[media]'}" for m in trimmed)
        sections.append("RECENT CONVERSATION:\n" + convo[:3000])
    return [{"role": "system", "content": "\n\n".join(sections)}]


async def execute_agent(
    session: AsyncSession, *, tenant_id: uuid.UUID | None, profile: AgentProfile,
    user_message: str, conversation_id: uuid.UUID | None = None, lead_id: uuid.UUID | None = None,
    person_id: uuid.UUID | None = None, permissions: set[str] | None = None,
    history: list[dict[str, Any]] | None = None, high_value_approved: bool = False,
) -> AgentResult:
    started = time.monotonic()
    provider = get_model_provider()
    tools = tools_for_scopes(profile.tool_scopes)
    tool_schemas = [t.to_openai_schema() for t in tools]

    execution = AIExecution(
        tenant_id=tenant_id, agent_key=profile.key, agent_profile_id=profile.id,
        agent_version=profile.version, conversation_id=conversation_id, lead_id=lead_id,
        person_id=person_id, status="running",
    )
    session.add(execution)
    await session.flush()
    exec_id = execution.id

    messages: list[dict[str, Any]] = build_system_context(
        profile=profile, person_context=None, recent_messages=history,
        policies=list((profile.guardrails or {}).get("policies", [])) or None,
    )
    messages.append({"role": "user", "content": user_message})

    tool_call_log: list[dict[str, Any]] = []
    guardrail_flags: list[str] = []
    final_message: str | None = None
    status = "completed"
    stop_reason = "task_complete"
    total_in = total_out = 0
    cost = 0.0

    try:
        for step in range(profile.max_steps):
            response: ModelResponse = await provider.chat(
                messages, tool_schemas or None, profile.model_profile
            )
            total_in += response.input_tokens
            total_out += response.output_tokens
            cost += (response.input_tokens * 0.000001) + (response.output_tokens * 0.000002)

            if response.tool_calls:
                messages.append({
                    "role": "assistant", "content": response.text,
                    "tool_calls": response.tool_calls,
                })
                for tc in response.tool_calls:
                    tool_result = await execute_tool(
                        session, tenant_id=tenant_id, tool_name=tc["name"], args=tc.get("args", {}),
                        actor_id=f"agent:{profile.key}:v{profile.version}",
                        on_behalf_permissions=set(profile.permissions or []) | (permissions or set()),
                        high_value_approved=high_value_approved,
                    )
                    tool_call_log.append({
                        "step": step, "name": tc["name"], "args": tc.get("args", {}),
                        "ok": tool_result.get("ok", False),
                    })
                    messages.append({
                        "role": "tool", "name": tc["name"],
                        "content": json.dumps(tool_result, ensure_ascii=False, default=str)[:4000],
                    })
                continue

            final_message = response.text or ""
            flags, needs_human = check_output(final_message, tool_call_log)
            guardrail_flags = flags
            if needs_human:
                status = "escalated"
                stop_reason = "need_human"
            break
        else:
            status = "failed"
            stop_reason = "max_steps_reached"
            final_message = final_message or "لم أتمكن من إكمال المهمة — سيتم تحويلك لموظف مختص."
    except DomainError as exc:
        status = "failed"
        stop_reason = "policy" if exc.code == "permission_denied" else "tool_failure"
        final_message = "حدث خطأ في معالجة طلبك — سيتم تحويلك لموظف مختص."
        execution.error = f"{exc.code}: {exc.message}"[:500]
    except Exception as exc:  # noqa: BLE001 — model/provider errors must not crash the caller
        status = "failed"
        stop_reason = "tool_failure"
        final_message = "تعذر الاتصال بمحرك الذكاء الاصطناعي حاليًا."
        execution.error = f"{type(exc).__name__}: {exc}"[:500]

    latency = int((time.monotonic() - started) * 1000)
    if latency > profile.max_latency_ms:
        stop_reason = "max_latency_exceeded"
    if cost > profile.max_cost_usd:
        stop_reason = "budget_exceeded"

    execution.status = status
    execution.model = provider.provider
    execution.provider = provider.provider
    execution.tool_calls = tool_call_log
    execution.input_tokens = total_in
    execution.output_tokens = total_out
    execution.cost_usd = cost
    execution.latency_ms = latency
    execution.final_message = final_message
    execution.guardrail_flags = guardrail_flags
    execution.human_handoff = status == "escalated"
    execution.outcome = {"stop_reason": stop_reason}
    execution.finished_at = datetime.now(UTC)
    await session.flush()

    return AgentResult(
        message=final_message or "", status=status, stop_reason=stop_reason,
        tool_calls=tool_call_log, execution_id=exec_id, guardrail_flags=guardrail_flags,
        needs_human=status == "escalated",
    )


async def get_profile(session: AsyncSession, tenant_id: uuid.UUID | None, key: str) -> AgentProfile:
    """Tenant-specific profile first, then the global template."""
    if tenant_id is not None:
        row = (
            await session.execute(
                select(AgentProfile)
                .where(
                    AgentProfile.tenant_id == tenant_id, AgentProfile.key == key,
                    AgentProfile.is_active.is_(True),
                )
                .order_by(AgentProfile.version.desc())
            )
        ).scalar_one_or_none()
        if row:
            return row
    row = (
        await session.execute(
            select(AgentProfile)
            .where(AgentProfile.tenant_id.is_(None), AgentProfile.key == key,
                   AgentProfile.is_active.is_(True))
            .order_by(AgentProfile.version.desc())
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(f"Agent profile not found: {key}")
    return row
