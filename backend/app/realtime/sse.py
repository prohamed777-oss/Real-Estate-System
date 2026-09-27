"""Real-time Subscription Gateway (V4 PART 14) — SSE transport.

Principle 1.31: real-time surfaces get a real-time plane. SSE chosen over WS
for serverless-friendliness and HTTP-native auth; the subscription registry +
NATS subject fan-out can be attached behind this same endpoint later.

Auth: bearer token via query param (?authorization=Bearer%20<jwt>) because
EventSource cannot set headers. Tenant-scoped: only that tenant's events flow.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.db import session_factory
from app.core.security import decode_token
from app.events.history import DomainEventHistory

router = APIRouter(prefix="/realtime", tags=["realtime"])


async def fetch_events(session, *, tenant_id: uuid.UUID, after_id: str | None = None):  # noqa: ANN001
    """Tenant-scoped event fetch (oldest→newest) — the testable core of the stream."""
    query = (
        select(DomainEventHistory)
        .where(DomainEventHistory.tenant_id == tenant_id)
        .order_by(DomainEventHistory.recorded_at.desc())
        .limit(50)
    )
    rows = list((await session.execute(query)).scalars().all())
    rows.reverse()
    if after_id:
        rows = [r for r in rows if str(r.event_id) > after_id]
    return rows


async def _resolve_tenant(request: Request) -> uuid.UUID | None:
    # dev mode: X-Dev-Email header (never in production)
    from app.core.config import settings as cfg

    dev_email = request.query_params.get("dev_email")
    if dev_email and cfg.auth_dev_enabled and not cfg.is_production:
        from sqlalchemy import select as sel

        from app.organizations.models import User

        async with session_factory() as s:
            user = (
                await s.execute(sel(User).where(User.email == dev_email))
            ).scalar_one_or_none()
        return user.tenant_id if user else None
    auth = request.query_params.get("authorization", "")
    if auth.startswith("Bearer "):
        claims = decode_token(auth.removeprefix("Bearer ").strip())
        sub = claims.get("sub")
        if not sub:
            return None
        from sqlalchemy import select as sel

        from app.organizations.models import User

        async with session_factory() as s:
            user = (await s.execute(sel(User).where(User.id == uuid.UUID(sub)))).scalar_one_or_none()
        return user.tenant_id if user else None
    return None


@router.get("/stream")
async def stream_events(request: Request, cursor: str | None = None):
    """SSE stream of the tenant's domain events from the given cursor."""
    tenant_id = await _resolve_tenant(request)
    if tenant_id is None:
        return StreamingResponse(iter(["event: error\ndata: unauthorized\n\n"]),
                                  media_type="text/event-stream", status_code=401)

    async def event_stream():
        last_seen = cursor
        heartbeat = 0
        while True:
            if await request.is_disconnected():
                return
            async with session_factory() as session:
                rows = await fetch_events(session, tenant_id=tenant_id, after_id=last_seen)
            for row in rows:
                payload = json.dumps({
                    "event_id": str(row.event_id),
                    "event_type": row.event_type,
                    "aggregate_type": row.aggregate_type,
                    "aggregate_id": str(row.aggregate_id) if row.aggregate_id else None,
                    "occurred_at": row.occurred_at.isoformat(),
                    "payload": row.payload,
                }, ensure_ascii=False, default=str)
                last_seen = str(row.event_id)
                yield f"id: {row.event_id}\nevent: domain_event\ndata: {payload}\n\n"
            heartbeat += 1
            if heartbeat % 15 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
