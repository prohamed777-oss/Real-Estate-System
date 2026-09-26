"""Decision Plane services (V4 PART 5) — composed, reproducible decisions.

In the modular monolith these are four composable services + an orchestrator,
each with a service-shaped interface (extractable later without redesign):
    Policy (deterministic gate) → Scoring (stateless) →
    Optimization (solver) → Approval (human primitive) → Decision Record.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, ValidationFailed
from app.decision.models import ApprovalRequest, Decision

POLICY_VERSION = "v1"


# ---------------------------------------------------------------- ① Policy
async def evaluate_policy(
    session: AsyncSession, *, tenant_id: uuid.UUID, action: str,
    subject: dict[str, Any], context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic policy gate. Returns {allow, obligations, policy_version}.

    Rules are declarative and versioned. <20ms by design — pure checks only.
    """
    rules: list[dict[str, Any]] = [
        {
            "action": "ASSIGN_LEAD",
            "effect": "allow",
            "obligations": ["fairness_quota"],
        },
        {
            "action": "APPLY_DISCOUNT",
            "effect": "deny_unless_approved",
            "condition": lambda s, c: float(s.get("discount_pct", 0)) > 5,
            "obligations": ["manager_approval"],
        },
        {
            "action": "PUBLISH_CONTENT",
            "effect": "deny_unless_approved",
            "condition": lambda s, c: True,  # content ALWAYS needs human approval (§42)
            "obligations": ["content_review"],
        },
    ]
    obligations: list[str] = []
    allow = True
    for rule in rules:
        if rule["action"] != action:
            continue
        cond = rule.get("condition")
        if cond is None or cond(subject, context or {}):
            if rule["effect"] == "deny_unless_approved":
                allow = False
            obligations.extend(rule.get("obligations", []))
    return {"allow": allow, "obligations": sorted(set(obligations)),
             "policy_version": POLICY_VERSION, "evaluated_at": datetime.now(UTC).isoformat()}


# ------------------------------------------------------------- ② Scoring
async def score_candidates(
    session: AsyncSession, *, candidates: list[dict[str, Any]],
    signal_names: list[str] | None = None, definition_version: str = "v1",
) -> list[dict[str, Any]]:
    """Stateless scoring over provided candidate attributes (signals).
    Definition version recorded so scores are reproducible."""
    scored = []
    for c in candidates:
        signals = c.get("signals", {})
        score = 0.0
        if "workload" in signals and "lead_value" in signals:
            load = float(signals.get("workload") or 0)
            value = float(signals.get("lead_value") or 0)
            score = max(0.0, min(1.0, (1.0 - min(load / 10.0, 1.0)) * 0.4
                                      + min(value / 5_000_000, 1.0) * 0.4
                                      + float(signals.get("expertise_fit", 0.5)) * 0.2))
        else:
            matched = [k for k in (signal_names or []) if signals.get(k)]
            score = (len(matched) / len(signal_names)) if signal_names else 0.5
        scored.append({"entity_id": c["entity_id"], "score": round(score, 4),
                        "contributing_signals": sorted(signals.keys()),
                        "definition_version": definition_version})
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored


# -------------------------------------------------------- ③ Optimization
async def optimize_selection(
    session: AsyncSession, *, scored: list[dict[str, Any]],
    objective: str = "maximize_conversion",
    constraints: dict[str, Any] | None = None,
    solver_version: str = "greedy-v1",
) -> dict[str, Any]:
    """Constraint-aware selection (greedy first-fit on ranked candidates).
    Constraints: {"capacity": {entity_id: int}, "fairness_min_score": float}"""
    constraints = constraints or {}
    capacity = dict(constraints.get("capacity", {}))
    fairness = float(constraints.get("fairness_min_score", 0.0))
    for c in scored:
        cid = c["entity_id"]
        if capacity.get(cid, 1) <= 0:
            continue
        if c["score"] < fairness:
            continue
        if capacity:
            capacity[cid] = capacity[cid] - 1
        return {"selected": cid, "objective": objective,
                 "objective_value": c["score"], "alternatives": scored[:5],
                 "solver_version": solver_version}
    return {"selected": None, "objective": objective, "objective_value": 0.0,
             "alternatives": scored[:5], "solver_version": solver_version}


# ------------------------------------------------ ④⑤ Orchestrator + Record
async def decide(
    session: AsyncSession, *, tenant_id: uuid.UUID, action: str,
    subject: dict[str, Any], candidates: list[dict[str, Any]] | None = None,
    objective: str = "maximize_conversion",
    constraints: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> Decision:
    """Compose ①→②→③, record the full Decision Record, return it.
    Approval (④) is NOT auto-created here — domains request approvals via the
    approval service when the policy outcome demands a human."""
    policy = await evaluate_policy(session, tenant_id=tenant_id, action=action,
                                    subject=subject, context=context)
    scoring: list[dict[str, Any]] = []
    optimization: dict[str, Any] = {}
    if candidates:
        scoring = await score_candidates(session, candidates=candidates)
        optimization = await optimize_selection(session, scored=scoring,
                                                 constraints=constraints)
    decision = Decision(
        tenant_id=tenant_id, action=action, reason=f"policy allow={policy['allow']}",
        confidence=(optimization.get("objective_value") if optimization else None),
        input_snapshot={"subject": subject, "context": context or {}},
        policy_evaluation=policy,
        scoring_results=scoring,
        optimization_result=optimization,
        selected_option=({"entity_id": optimization["selected"]} if optimization.get("selected") else {}),
    )
    session.add(decision)
    await session.flush()
    return decision


# ---------------------------------------------------------------- ④ Approval
async def request_approval(
    session: AsyncSession, *, tenant_id: uuid.UUID, subject_type: str,
    subject_id: str, payload: dict[str, Any] | None = None,
    requested_by: str | None = None, approver_role_required: str | None = None,
    policy_reference: str | None = None, expires_in_days: int = 7,
) -> ApprovalRequest:
    request = ApprovalRequest(
        tenant_id=tenant_id, subject_type=subject_type, subject_id=subject_id,
        requested_by=requested_by, payload_snapshot=payload or {},
        policy_reference=policy_reference,
        approver_role_required=approver_role_required,
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
    )
    session.add(request)
    await session.flush()
    return request


async def decide_approval(
    session: AsyncSession, *, tenant_id: uuid.UUID, approval_id: uuid.UUID,
    decision: str, decided_by: str, reason: str | None = None,
    required_role: str | None = None, actor_role: str | None = None,
) -> ApprovalRequest:
    if decision not in ("APPROVED", "REJECTED"):
        raise ValidationFailed("decision must be APPROVED or REJECTED")
    request = await session.get(ApprovalRequest, approval_id)
    if request is None or request.tenant_id != tenant_id:
        raise ValidationFailed("Approval request not found")
    if request.status != "PENDING":
        raise Conflict(f"Approval already {request.status}")
    if request.approver_role_required and actor_role and actor_role != request.approver_role_required:
        raise ValidationFailed(f"Requires role {request.approver_role_required}")
    if request.expires_at and request.expires_at < datetime.now(UTC):
        request.status = "EXPIRED"
        await session.flush()
        raise Conflict("Approval request expired")
    request.status = decision
    request.decided_by = decided_by
    request.decided_at = datetime.now(UTC)
    request.decision_reason = reason
    await session.flush()
    return request


async def get_pending_approval(session: AsyncSession, *, tenant_id: uuid.UUID,
                               subject_type: str, subject_id: str) -> ApprovalRequest | None:
    return (
        await session.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.tenant_id == tenant_id,
                ApprovalRequest.subject_type == subject_type,
                ApprovalRequest.subject_id == subject_id,
                ApprovalRequest.status == "PENDING",
            )
        )
    ).scalar_one_or_none()
