"""Invariant (§102): DB commit → event cannot silently disappear (§47).
Plus retry/backoff/dead-letter behavior (§50) and exactly-once consumption (§48).

Test convention: every DB operation on the shared `db` session runs inside an
explicit `async with db.begin():` block — never mix autobegin with explicit begin.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app.core.db import session_factory
from app.events.models import Job, OutboxEvent
from app.events.outbox import dispatch_batch, emit
from app.events.queue import enqueue, requeue_expired_leases, run_batch
from app.events.registry import event_handler, job_handler


async def _dispatch_in_new_session() -> dict:
    async with session_factory() as s:
        async with s.begin():
            return await dispatch_batch(s)


async def _run_jobs_in_new_session() -> dict:
    async with session_factory() as s:
        async with s.begin():
            return await run_batch(s)


async def _fetch_event(event_name: str) -> OutboxEvent:
    """Read event state from a FRESH session — the shared `db` session's
    identity map holds pre-dispatch snapshots (expire_on_commit=False)."""
    async with session_factory() as s:
        return (
            await s.execute(select(OutboxEvent).where(OutboxEvent.event_name == event_name))
        ).scalar_one()


async def _fetch_job(job_type: str) -> Job:
    async with session_factory() as s:
        return (await s.execute(select(Job).where(Job.type == job_type))).scalar_one()


async def test_committed_event_survives_and_dispatches(db, tenant):
    seen: list[dict] = []

    @event_handler("test.seen")
    async def h(session, envelope):  # noqa: ANN001
        seen.append(envelope["payload"])

    async with db.begin():
        await emit(db, event_name="test.seen", tenant_id=tenant.id, payload={"x": 1}, aggregate_type="t")
        count = (
            await db.execute(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.event_name == "test.seen"))
        ).scalar_one()
    assert count == 1, "event must be persisted in the same transaction"

    stats = await _dispatch_in_new_session()
    assert stats["processed"] >= 1  # our event (+ any other pending, e.g. tenant.provisioned)
    assert seen == [{"x": 1}]

    # re-dispatch must NOT re-handle (exactly-once, §48)
    stats2 = await _dispatch_in_new_session()
    assert seen == [{"x": 1}]  # handler did NOT run again
    row = await _fetch_event("test.seen")
    assert row.status == "processed"


async def test_rollback_erases_event(db, tenant):
    try:
        async with db.begin():
            await emit(db, event_name="test.rollback", tenant_id=tenant.id, payload={})
            raise RuntimeError("business failure")
    except RuntimeError:
        pass
    async with db.begin():
        count = (
            await db.execute(
                select(func.count()).select_from(OutboxEvent).where(OutboxEvent.event_name == "test.rollback")
            )
        ).scalar_one()
    assert count == 0, "rolled-back transaction must not leave events behind"


async def test_failure_retries_then_dead_letters(db, tenant):
    calls = {"n": 0}

    @event_handler("test.always_fails")
    async def boom(session, envelope):  # noqa: ANN001
        calls["n"] += 1
        raise RuntimeError(f"boom {calls['n']}")

    async with db.begin():
        await emit(db, event_name="test.always_fails", tenant_id=tenant.id, payload={})
        row = (
            await db.execute(select(OutboxEvent).where(OutboxEvent.event_name == "test.always_fails"))
        ).scalar_one()
        row.max_attempts = 2

    # attempt 1 → backoff pending
    stats1 = await _dispatch_in_new_session()
    assert stats1["failed"] == 1
    # zero out backoff so attempt 2 is claimable immediately
    async with db.begin():
        await db.execute(
            update(OutboxEvent)
            .where(OutboxEvent.event_name == "test.always_fails")
            .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    stats2 = await _dispatch_in_new_session()
    assert stats2["failed"] == 1

    row = await _fetch_event("test.always_fails")
    assert row.status == "dead"
    assert row.attempts == 2
    assert row.last_error is not None


async def test_job_queue_lease_retry_deadletter_and_dedup(db, tenant):
    runs: list[int] = []

    @job_handler("test.ok_job")
    async def ok_job(session, tenant_id, payload):  # noqa: ANN001
        runs.append(payload.get("n", 0))

    @job_handler("test.failing_job")
    async def bad_job(session, tenant_id, payload):  # noqa: ANN001
        raise RuntimeError("nope")

    @job_handler("test.sync")
    async def sync_job(session, tenant_id, payload):  # noqa: ANN001
        return None

    async with db.begin():
        await enqueue(db, job_type="test.ok_job", tenant_id=tenant.id, payload={"n": 7})
        await enqueue(db, job_type="test.failing_job", tenant_id=tenant.id, payload={}, max_attempts=1)
        dedup1 = await enqueue(db, job_type="test.sync", tenant_id=tenant.id, unique_key="asset-1")
        dedup2 = await enqueue(db, job_type="test.sync", tenant_id=tenant.id, unique_key="asset-1")
        assert dedup1 == dedup2, "unique_key must dedup pending jobs"
        pending = (
            await db.execute(select(func.count()).select_from(Job).where(Job.type == "test.sync"))
        ).scalar_one()
        assert pending == 1

    stats = await _run_jobs_in_new_session()
    assert stats["completed"] == 2
    assert stats["failed"] == 1
    assert runs == [7]

    dead = await _fetch_job("test.failing_job")
    assert dead.status == "dead"


async def test_expired_lease_is_requeued(db, tenant):
    async with db.begin():
        job_id = await enqueue(db, job_type="test.stuck", tenant_id=tenant.id, payload={})
        job = await db.get(Job, job_id)
        job.status = "running"
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)  # abandoned lease

    async with db.begin():
        requeued = await requeue_expired_leases(db)
    assert requeued == 1

    db.expire_all()
    async with db.begin():
        job = await db.get(Job, job_id)
        assert job.status == "pending"
