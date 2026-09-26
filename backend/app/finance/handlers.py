"""Finance event handlers: deal closure + usage metering."""

from __future__ import annotations

import uuid

from app.events.registry import event_handler


@event_handler("payment.completed")
async def on_payment_completed(session, envelope):  # noqa: ANN001
    """Meter usage for billing (§99)."""
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    if tenant_id is None:
        return
    from app.analytics.models import UsageMeter

    session.add(UsageMeter(tenant_id=tenant_id, kind="payment", quantity=1))
    await session.flush()
