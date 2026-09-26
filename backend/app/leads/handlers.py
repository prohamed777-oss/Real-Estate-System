"""Lead event handlers: auto-qualification pipeline hooks (M2 scope)."""

from __future__ import annotations

import uuid

from app.events.registry import event_handler


@event_handler("lead.created")
async def on_lead_created(session, envelope):  # noqa: ANN001
    """Auto-assign NEW leads to the tenant's default round-robin pool (M2 basic
    routing; §28 expertise-based routing lands in M5)."""
    payload = envelope["payload"]
    if payload.get("owner_id") or not envelope.get("tenant_id"):
        return
    from sqlalchemy import select

    from app.leads.models import Lead
    from app.leads.service import assign_lead

    tenant_id = uuid.UUID(envelope["tenant_id"])
    lead = (
        await session.execute(
            select(Lead).where(Lead.id == uuid.UUID(payload["lead_id"]), Lead.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if lead is None or lead.owner_id is not None:
        return
    # Light-weight fallback: leave unassigned when no active sales members exist.
    from app.organizations.models import Membership, Role, User

    sales_users = (
        await session.execute(
            select(User.id)
            .join(Membership, Membership.user_id == User.id)
            .join(Role, Role.id == Membership.role_id)
            .where(
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
                Role.key.in_(("sales", "sales_manager")),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if sales_users is not None:
        await assign_lead(
            session, lead=lead, owner_id=sales_users, actor_type="system",
            reason="auto-assign on creation",
        )


@event_handler("lead.qualified")
async def on_lead_qualified(session, envelope):  # noqa: ANN001
    """Qualified leads enter the matching pipeline (M4 handler registers here)."""
    return None
