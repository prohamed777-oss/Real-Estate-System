"""Audit trail (§94).

who / what / when / entity / before / after / source / request_id.
Written inside the SAME transaction as the mutation — an audited change that
rolled back must not appear, and an un-audited critical mutation must fail.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import AuditLog

CRITICAL_ACTIONS = {
    "price.changed",
    "inventory.changed",
    "lead.assigned",
    "permissions.changed",
    "reservation.created",
    "reservation.cancelled",
    "offer.created",
    "offer.approved",
    "contract.created",
    "payment.recorded",
    "commission.approved",
    "ai.action",
    "auth.role_changed",
}


async def audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_type: str,  # user | system | agent
    actor_id: uuid.UUID | str | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    source: str = "api",  # api | automation | ai | webhook | import | seed
    request_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=str(actor_id) if actor_id else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            before=before,
            after=after,
            source=source,
            request_id=request_id,
            trace_id=trace_id,
            occurred_at=datetime.now(UTC),
        )
    )
