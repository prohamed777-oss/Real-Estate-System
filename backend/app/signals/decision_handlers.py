"""Decision Plane integration: lead routing via Orchestrator (V4 5.5)."""

from __future__ import annotations

import uuid

from app.events.registry import event_handler


@event_handler("lead.created")
async def on_lead_created_decision_routing(session, envelope):  # noqa: ANN001
    """Lead created → Decision Plane assigns the best sales rep (V4 5.5).

    Replaces the simple round-robin with Policy→Scoring→Optimization→Record.
    """
    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None or payload.get("owner_id"):
        return

    from sqlalchemy import select

    from app.decision.services import decide
    from app.leads.models import Lead
    from app.leads.service import assign_lead
    from app.organizations.models import Membership, Role, User

    lead_id = uuid.UUID(payload["lead_id"])
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if lead is None or lead.owner_id is not None:
        return

    # get active sales reps as candidates
    reps = (
        await session.execute(
            select(User.id)
            .join(Membership, Membership.user_id == User.id)
            .join(Role, Role.id == Membership.role_id)
            .where(
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
                Role.key.in_(("sales", "sales_manager")),
            )
        )
    ).scalars().all()
    if not reps:
        return

    # build candidates with signal-based attributes
    candidates = []
    for rep_id in reps:
        # count active leads as workload signal
        from sqlalchemy import func

        workload = (
            await session.execute(
                select(func.count()).select_from(Lead).where(
                    Lead.owner_id == rep_id,
                    Lead.lifecycle_stage.notin_(("CONVERTED", "DISQUALIFIED")),
                )
            )
        ).scalar_one()
        candidates.append({
            "entity_id": str(rep_id),
            "signals": {"workload": workload, "lead_value": 3000000, "expertise_fit": 0.5},
        })

    # Decision Plane: ① policy → ② scoring → ③ optimization → ⑤ record
    decision = await decide(
        session, tenant_id=tenant_id, action="ASSIGN_LEAD",
        subject={"lead_id": str(lead_id)},
        candidates=candidates,
        objective="maximize_conversion",
    )
    selected_id = decision.selected_option.get("entity_id")
    if selected_id:
        await assign_lead(
            session, lead=lead, owner_id=uuid.UUID(selected_id),
            actor_type="system", actor_id=f"decision:{decision.id}",
            reason="Decision Plane routing",
        )
