"""Lead application services — commands & queries (review rule #4).

Rules enforced here (§8-9, §11):
- Lead lifecycle changes ONLY via the state machine through a domain service
- Every transition is audited and emits a domain event
- The LLM never changes lifecycle directly
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenancy
from app.core.audit import audit
from app.core.errors import NotFound, ValidationFailed
from app.events.outbox import emit
from app.identity.models import Person
from app.leads.models import Lead, LeadRequirement
from app.leads.scoring import compute_scores
from app.leads.statemachine import LEAD_EVENTS, LeadStateMachine


async def create_lead(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    person_id: uuid.UUID,
    source: str | None = None,
    campaign_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    created_by: str = "system",
    actor_id: uuid.UUID | str | None = None,
    explicit_requirements: dict[str, Any] | None = None,
) -> Lead:
    lead = Lead(
        tenant_id=tenant_id,
        person_id=person_id,
        source=source,
        campaign_id=campaign_id,
        owner_id=owner_id,
        lifecycle_stage="NEW",
        created_by=created_by,
    )
    session.add(lead)
    await session.flush()
    session.add(
        LeadRequirement(
            tenant_id=tenant_id,
            lead_id=lead.id,
            explicit=explicit_requirements or {},
            source="manual" if created_by == "user" else created_by,
        )
    )
    await audit(
        session, tenant_id=tenant_id, actor_type="user" if created_by == "user" else "system",
        actor_id=actor_id, action="lead.created", entity_type="lead", entity_id=lead.id,
        after={"person_id": str(person_id), "source": source}, source=created_by,
    )
    await emit(
        session, event_name="lead.created", tenant_id=tenant_id, aggregate_type="lead",
        aggregate_id=lead.id,
        payload={"lead_id": str(lead.id), "person_id": str(person_id), "source": source,
                 "owner_id": str(owner_id) if owner_id else None},
    )
    return lead


async def get_lead(session: AsyncSession, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> Lead:
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if lead is None:
        raise NotFound("Lead not found")
    return lead


ALLOWED_TRANSITION_ACTORS = {"user", "system", "automation"}  # AI acts only via approved tools


async def transition_lead(
    session: AsyncSession,
    *,
    lead: Lead,
    event: str,
    actor_type: str = "user",
    actor_id: uuid.UUID | str | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
) -> Lead:
    if actor_type not in ALLOWED_TRANSITION_ACTORS:
        raise ValidationFailed(f"Actor type {actor_type!r} may not change lead lifecycle")
    sm = LeadStateMachine(lead.lifecycle_stage)
    target = sm.fire(event)
    before = {"lifecycle_stage": lead.lifecycle_stage}
    lead.lifecycle_stage = target
    now_fields: dict[str, Any] = {}
    if target == "DORMANT":
        from datetime import UTC, datetime

        lead.dormant_since = datetime.now(UTC)
    if target in ("DISQUALIFIED",):
        lead.lost_reason = reason
    await audit(
        session, tenant_id=lead.tenant_id, actor_type=actor_type, actor_id=actor_id,
        action="lead.lifecycle_changed", entity_type="lead", entity_id=lead.id,
        before=before, after={"lifecycle_stage": target, "event": event, "reason": reason},
    )
    event_name = LEAD_EVENTS.get(event, f"lead.{event}")
    await emit(
        session, event_name=event_name, tenant_id=lead.tenant_id, aggregate_type="lead",
        aggregate_id=lead.id, trace_id=trace_id,
        payload={"lead_id": str(lead.id), "from": before["lifecycle_stage"], "to": target,
                 "reason": reason},
    )
    return lead


async def assign_lead(
    session: AsyncSession, *, lead: Lead, owner_id: uuid.UUID, team_id: uuid.UUID | None = None,
    actor_type: str = "user", actor_id: uuid.UUID | str | None = None, reason: str | None = None,
) -> Lead:
    before = {"owner_id": str(lead.owner_id) if lead.owner_id else None}
    lead.owner_id = owner_id
    if team_id:
        lead.team_id = team_id
    await audit(
        session, tenant_id=lead.tenant_id, actor_type=actor_type, actor_id=actor_id,
        action="lead.assigned", entity_type="lead", entity_id=lead.id, before=before,
        after={"owner_id": str(owner_id), "reason": reason},
    )
    await emit(
        session, event_name="lead.assigned", tenant_id=lead.tenant_id, aggregate_type="lead",
        aggregate_id=lead.id,
        payload={"lead_id": str(lead.id), "owner_id": str(owner_id), "previous_owner": before["owner_id"],
                 "reason": reason},
    )
    return lead


async def update_requirements(
    session: AsyncSession, *, lead_id: uuid.UUID, explicit: dict[str, Any] | None = None,
    behavioral: dict[str, Any] | None = None, objections: list | None = None,
    timeline: str | None = None, confidence: int | None = None, source: str = "manual",
) -> LeadRequirement:
    req = (
        await session.execute(
            select(LeadRequirement).where(
                LeadRequirement.lead_id == lead_id,
                LeadRequirement.tenant_id == tenancy.current_tenant(),
            )
        )
    ).scalar_one_or_none()
    if req is None:
        raise NotFound("Lead requirements not found")
    if explicit is not None:
        req.explicit = {**req.explicit, **explicit}
    if behavioral is not None:
        req.behavioral = {**req.behavioral, **behavioral}
    if objections is not None:
        req.objections = objections
    if timeline is not None:
        req.timeline = timeline
    if confidence is not None:
        req.confidence = confidence
    req.source = source
    await session.flush()
    await emit(
        session, event_name="lead.requirements_updated", tenant_id=req.tenant_id,
        aggregate_type="lead", aggregate_id=lead_id,
        payload={"lead_id": str(lead_id), "source": source},
    )
    return req


async def recompute_scores(session: AsyncSession, *, lead: Lead) -> Lead:
    """Deterministic scoring — AI interprets results, never solely sources them (§11).

    Activity/inventory-fit signals arrive via lead.score_signals and are updated
    by event handlers (conversations, matching) — the engine itself is pure.
    """
    req = (
        await session.execute(
            select(LeadRequirement).where(LeadRequirement.lead_id == lead.id)
        )
    ).scalar_one_or_none()
    signals = lead.score_signals or {}
    activity: dict[str, Any] = signals.get("activity", {})
    inventory_fit: dict[str, Any] = signals.get("inventory_fit", {})
    result = compute_scores(
        lead={
            "owner_id": str(lead.owner_id) if lead.owner_id else None,
            "source": lead.source,
        },
        requirements=(req.explicit if req else {}) or {},
        activity=activity,
        inventory_fit=inventory_fit,
    )
    lead.lead_score = result["lead_score"]
    lead.intent_score = result["intent_score"]
    lead.engagement_score = result["engagement_score"]
    lead.fit_score = result["fit_score"]
    lead.score_version = result["score_version"]
    lead.score_signals = result["score_signals"]
    lead.score_calculated_at = result["score_calculated_at"]
    await session.flush()
    await emit(
        session, event_name="lead.score_changed", tenant_id=lead.tenant_id, aggregate_type="lead",
        aggregate_id=lead.id,
        payload={"lead_id": str(lead.id), "lead_score": lead.lead_score,
                 "intent_score": lead.intent_score, "engagement_score": lead.engagement_score,
                 "fit_score": lead.fit_score, "score_version": lead.score_version},
    )
    return lead
