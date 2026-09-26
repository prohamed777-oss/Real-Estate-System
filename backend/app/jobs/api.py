"""Internal cron endpoints — Vercel Cron / local runner entry point.

One tick does, in order:
    1. requeue abandoned leases (worker crash recovery)
    2. dispatch transactional outbox events
    3. claim & run a batch of jobs

In production this is protected by the X-Cron-Secret header.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import session_factory
from app.events.outbox import dispatch_batch
from app.events.queue import requeue_expired_leases, run_batch

router = APIRouter(prefix="/internal/jobs", tags=["internal"])


async def _tick(session: AsyncSession) -> dict[str, Any]:
    requeued = await requeue_expired_leases(session)
    outbox_stats = await dispatch_batch(session)
    job_stats = await run_batch(session)
    return {"requeued_leases": requeued, "outbox": outbox_stats, "jobs": job_stats}


@router.post("/tick")
async def tick(x_cron_secret: str = Header(default="")) -> dict[str, Any]:
    if x_cron_secret != settings.cron_secret:
        raise HTTPException(status_code=401, detail="Invalid cron secret")
    async with session_factory() as session:
        async with session.begin():
            result = await _tick(session)
    return {"status": "ok", **result}
