"""Gap 1 fix: handlers for ALL 21 orphaned events.

Every handler connects an existing event to an existing action.
No new features — just closing the automation loop.
"""

from __future__ import annotations

import uuid

from app.events.registry import event_handler


def _tid(envelope) -> uuid.UUID | None:  # noqa: ANN001
    return uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None


# ===== finance chain =====
@event_handler("commission.created")
async def on_commission_created(session, envelope):  # noqa: ANN001
    """Commission calculated → notify finance team for approval."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="commission.created",
        payload=envelope["payload"],
    )


@event_handler("contract.created")
async def on_contract_created(session, envelope):  # noqa: ANN001
    """Contract created → notify operations + start payment reminders."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="contract.created",
        payload=envelope["payload"],
    )


@event_handler("deal.created")
async def on_deal_created(session, envelope):  # noqa: ANN001
    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    from app.automation.service import apply_rules_for_event

    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="deal.created",
        payload=envelope["payload"],
    )


@event_handler("payment.scheduled")
async def on_payment_scheduled(session, envelope):  # noqa: ANN001
    """Payment scheduled → create a follow-up task before the due date."""
    from datetime import UTC, datetime, timedelta

    from app.automation.service import apply_rules_for_event
    from app.conversations.models import Task

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    payload = envelope["payload"]
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="payment.scheduled", payload=payload
    )
    # create a reminder task 3 days before due date
    if payload.get("due_at"):
        due = datetime.fromisoformat(payload["due_at"])
        reminder_at = due - timedelta(days=3)
        if reminder_at > datetime.now(UTC):
            session.add(Task(
                tenant_id=tenant_id, title=f"متابعة دفعة #{payload.get('installment_no', '')}",
                type="payment_reminder", priority="high",
                due_at=reminder_at, created_by="automation",
                entity_type="deal", entity_id=uuid.UUID(payload["deal_id"]) if payload.get("deal_id") else None,
            ))


# ===== sales chain =====
@event_handler("offer.created")
async def on_offer_created(session, envelope):  # noqa: ANN001
    """Offer created → notify sales manager if discount > threshold."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="offer.created",
        payload=envelope["payload"],
    )


@event_handler("opportunity.created")
async def on_opportunity_created(session, envelope):  # noqa: ANN001
    """Opportunity created → check staleness + notify team."""
    from app.automation.service import apply_rules_for_event
    from app.signals.service import check_projection_staleness

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="opportunity.created",
        payload=envelope["payload"],
    )
    try:
        await check_projection_staleness(
            session, tenant_id=tenant_id, projection_name="property_search_documents"
        )
    except Exception:  # noqa: BLE001
        pass


@event_handler("viewing.requested")
async def on_viewing_requested(session, envelope):  # noqa: ANN001
    """Viewing requested → create confirmation task for sales rep."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="viewing.requested",
        payload=envelope["payload"],
    )


# ===== engagement chain =====
@event_handler("conversation.created")
async def on_conversation_created(session, envelope):  # noqa: ANN001
    """New conversation → check if AI should handle it."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="conversation.created",
        payload=envelope["payload"],
    )


@event_handler("conversation.assigned")
async def on_conversation_assigned(session, envelope):  # noqa: ANN001
    """Conversation assigned → notify the assignee."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="conversation.assigned",
        payload=envelope["payload"],
    )


@event_handler("person.created")
async def on_person_created(session, envelope):  # noqa: ANN001
    """New person from inbound channel → check for duplicates."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="person.created",
        payload=envelope["payload"],
    )


@event_handler("person.merged")
async def on_person_merged(session, envelope):  # noqa: ANN001
    """Person merged → recompute lead scores for affected leads."""
    from sqlalchemy import select

    from app.leads.models import Lead
    from app.leads.service import recompute_scores

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    payload = envelope["payload"]
    primary_id = uuid.UUID(payload["primary_id"])
    leads = (
        await session.execute(
            select(Lead).where(Lead.person_id == primary_id, Lead.tenant_id == tenant_id)
        )
    ).scalars().all()
    for lead in leads:
        await recompute_scores(session, lead=lead)


# ===== property chain =====
@event_handler("inventory.conflict_detected")
async def on_inventory_conflict(session, envelope):  # noqa: ANN001
    """Conflict detected → notify operations team."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="inventory.conflict_detected",
        payload=envelope["payload"],
    )


@event_handler("matching.completed")
async def on_matching_completed(session, envelope):  # noqa: ANN001
    """Matching completed → update lead stage if matches found."""
    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from sqlalchemy import select

    from app.leads.models import Lead
    from app.leads.service import transition_lead

    lead = (
        await session.execute(
            select(Lead).where(Lead.id == uuid.UUID(payload["lead_id"]), Lead.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if lead is None or lead.lifecycle_stage != "QUALIFYING":
        return
    sm = __import__("app.leads.statemachine", fromlist=["LeadStateMachine"]).LeadStateMachine(
        lead.lifecycle_stage
    )
    if "qualified" in sm.allowed_events() and payload.get("top", 0) > 0:
        await transition_lead(session, lead=lead, event="qualified",
                              actor_type="automation", reason="matches found")


@event_handler("lead.score_changed")
async def on_lead_score_changed(session, envelope):  # noqa: ANN001
    """Score changed → update next_action based on new score."""
    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from sqlalchemy import select

    from app.leads.models import Lead

    lead = (
        await session.execute(
            select(Lead).where(Lead.id == uuid.UUID(payload["lead_id"]), Lead.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if lead is None:
        return
    score = lead.lead_score
    if score >= 80:
        lead.next_action = "CALL"
    elif score >= 60:
        lead.next_action = "SEND_PROPERTIES"
    elif score >= 40:
        lead.next_action = "FOLLOW_UP"
    else:
        lead.next_action = "WAIT"
    await session.flush()


@event_handler("lead.assigned")
async def on_lead_assigned_notify(session, envelope):  # noqa: ANN001
    """Lead assigned → notify the new owner."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="lead.assigned",
        payload=envelope["payload"],
    )


@event_handler("message.sent")
async def on_message_sent(session, envelope):  # noqa: ANN001
    """Message sent → track engagement signal."""
    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    from app.automation.service import apply_rules_for_event

    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="message.sent",
        payload=envelope["payload"],
    )


@event_handler("message.failed")
async def on_message_failed(session, envelope):  # noqa: ANN001
    """Message failed → create recovery task."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="message.failed",
        payload=envelope["payload"],
    )


# ===== platform chain =====
@event_handler("tenant.provisioned")
async def on_tenant_provisioned(session, envelope):  # noqa: ANN001
    """Tenant provisioned → seed default agent profiles + signal definitions."""
    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    from app.ai.agents import seed_default_profiles

    await seed_default_profiles(session)


# ===== documents chain =====
@event_handler("document.signed")
async def on_document_signed(session, envelope):  # noqa: ANN001
    """Document signed → notify all parties."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="document.signed",
        payload=envelope["payload"],
    )


@event_handler("document.version_added")
async def on_document_version_added(session, envelope):  # noqa: ANN001
    """New version → invalidate prior approvals (handled in service) + notify."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="document.version_added",
        payload=envelope["payload"],
    )


# ===== marketing chain =====
@event_handler("content.published")
async def on_content_published(session, envelope):  # noqa: ANN001
    """Content published → notify marketing team."""
    from app.automation.service import apply_rules_for_event

    tenant_id = _tid(envelope)
    if tenant_id is None:
        return
    await apply_rules_for_event(
        session, tenant_id=tenant_id, event_name="content.published",
        payload=envelope["payload"],
    )
