"""V4 4.3: optional NATS JetStream transport.

The durable history (domain_events) + outbox dispatcher remain the truth.
When NATS_URL is configured, the dispatcher ALSO publishes events to
tenant-scoped subjects (`tenant.{id}.{event_name}`) for external consumers.
Missing/unreachable NATS never blocks the event pipeline.
"""

from __future__ import annotations

import json
import logging
import uuid

from app.core.config import settings

log = logging.getLogger("revenue_os.nats")

_publisher = None
_tried = False


async def get_publisher():
    """Lazy NATS connection (nats-py). Returns None when not configured."""
    global _publisher, _tried
    if _tried:
        return _publisher
    _tried = True
    url = getattr(settings, "nats_url", "") or ""
    if not url:
        return None
    try:
        import nats

        _publisher = await nats.connect(url, name="revenue-os-events")
        log.info("NATS connected: %s", url)
    except Exception as exc:  # noqa: BLE001 — transport failure must not break events
        log.warning("NATS unavailable (%s) — events continue in-process", exc)
        _publisher = None
    return _publisher


def subject_for(tenant_id: uuid.UUID | None, event_name: str) -> str:
    tenant = str(tenant_id) if tenant_id else "system"
    return f"tenant.{tenant}.{event_name}"


async def publish_event(event_name: str, tenant_id: uuid.UUID | None,
                        envelope: dict) -> bool:
    """Best-effort publish. Returns False when NATS is not in the loop."""
    pub = await get_publisher()
    if pub is None:
        return False
    try:
        await pub.publish(subject_for(tenant_id, event_name),
                           json.dumps(envelope, default=str).encode())
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("NATS publish failed: %s", exc)
        return False
