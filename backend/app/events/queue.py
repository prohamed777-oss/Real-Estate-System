"""Postgres-backed job queue with lease/retry/backoff/DLQ (review rule #1).

The queue is NOT a naive table:
- claim with FOR UPDATE SKIP LOCKED (safe with N workers)
- lease_until + requeue of expired leases (worker crash recovery)
- attempts/max_attempts with exponential backoff
- dead-letter status ('dead') for exhausted jobs
- optional unique_key dedup per (tenant, type)

Swap to Redis/broker later WITHOUT touching domain code — domains only call
`enqueue()` (review note: implementation detail behind an interface).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.models import Job, JobLeaseRequeue
from app.events.registry import JOB_HANDLERS

log = logging.getLogger("revenue_os.jobs")

DEFAULT_LEASE_SECONDS = 120
WORKER_ID_PREFIX = "worker"


async def enqueue(
    session: AsyncSession,
    *,
    job_type: str,
    tenant_id: uuid.UUID | None,
    payload: dict[str, Any] | None = None,
    priority: int = 5,
    delay_seconds: int = 0,
    unique_key: str | None = None,
    max_attempts: int = 8,
) -> uuid.UUID:
    """Enqueue a job. With unique_key, an equivalent pending/running job is reused."""
    if unique_key:
        stmt = (
            select(Job)
            .where(
                Job.tenant_id == tenant_id,
                Job.type == job_type,
                Job.unique_key == unique_key,
                Job.status.in_(("pending", "running")),
            )
            .limit(1)
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing.id

    job = Job(
        type=job_type,
        tenant_id=tenant_id,
        payload=payload or {},
        priority=priority,
        available_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
        unique_key=unique_key,
        max_attempts=max_attempts,
    )
    session.add(job)
    await session.flush()
    return job.id


async def requeue_expired_leases(session: AsyncSession) -> int:
    """Return abandoned jobs (worker died mid-lease) to the pending state."""
    res = await session.execute(
        text(
            """
            UPDATE jobs
            SET status = 'pending', locked_at = NULL, locked_by = NULL, lease_until = NULL
            WHERE status = 'running' AND lease_until < now()
            RETURNING id
            """
        )
    )
    ids = res.scalars().all()
    for jid in ids:
        session.add(JobLeaseRequeue(job_id=jid, reason="lease_expired"))
    await session.flush()
    return len(ids)


def _backoff(attempts: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=min(3600, 5 * (2 ** max(0, attempts))))


async def run_batch(
    session: AsyncSession,
    *,
    worker_id: str | None = None,
    batch: int = 20,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> dict[str, int]:
    """Claim and execute jobs. Each job executes in its own SAVEPOINT."""
    worker = worker_id or f"{WORKER_ID_PREFIX}-{uuid.uuid4().hex[:8]}"
    claimed_ids = (
        (
            await session.execute(
                text(
                    """
                    SELECT id FROM jobs
                    WHERE status = 'pending' AND available_at <= now()
                    ORDER BY priority, created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT :batch
                    """
                ),
                {"batch": batch},
            )
        )
        .scalars()
        .all()
    )
    stats = {"completed": 0, "failed": 0}

    for job_id in claimed_ids:
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
        # tenant GUC for this unit of work (RLS-ready workers, §82)
        if job.tenant_id is not None:
            # transaction-local: is_local=false leaked the tenant onto the pooled
            # connection after commit (stale GUC for the next borrower, §82)
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(job.tenant_id)},
            )
        job.status = "running"
        job.locked_at = datetime.now(UTC)
        job.locked_by = worker
        job.lease_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        await session.flush()

        handler = JOB_HANDLERS.get(job.type)
        try:
            async with session.begin_nested():
                if handler is None:
                    raise LookupError(f"No handler registered for job type {job.type!r}")
                await handler(session, job.tenant_id, job.payload)
                job.status = "completed"
                job.completed_at = datetime.now(UTC)
                job.lease_until = None
                stats["completed"] += 1
        except Exception as exc:  # noqa: BLE001
            job.attempts += 1
            job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            if job.attempts >= job.max_attempts:
                job.status = "dead"
                log.error("job %s (%s) dead-lettered: %s", job.id, job.type, job.last_error)
            else:
                job.status = "pending"
                job.available_at = _backoff(job.attempts)
                job.lease_until = None
                log.warning("job %s (%s) failed attempt %s: %s", job.id, job.type, job.attempts, exc)
            stats["failed"] += 1

    await session.flush()
    return stats
