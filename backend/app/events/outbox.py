"""Transactional outbox: emit + dispatch (§47, §48, §50)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.events.models import OutboxEvent, ProcessedEvent
from app.events.registry import EVENT_HANDLERS

log = logging.getLogger("revenue_os.outbox")

CONSUMER_ID = "outbox_dispatcher"


class DomainEventEnvelope(dict):
    pass


async def emit(
    session: AsyncSession,
    *,
    event_name: str,
    tenant_id: uuid.UUID | None,
    payload: dict[str, Any],
    aggregate_type: str | None = None,
    aggregate_id: uuid.UUID | None = None,
    event_version: int = 1,
    trace_id: str | None = None,
    causation_id: str | None = None,
) -> uuid.UUID:
    """Insert an outbox row INSIDE the caller's transaction (§47).

    If the business transaction commits, the event survives; if it rolls back,
    the event never existed. There is no path where the DB state changes while
    the event silently disappears.
    """
    if not event_name or len(event_name) > 150:
        raise DomainError(f"Invalid event name: {event_name!r}")
    row = OutboxEvent(
        event_name=event_name,
        event_version=event_version,
        tenant_id=tenant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        trace_id=trace_id,
        causation_id=causation_id,
    )
    session.add(row)
    await session.flush()
    return row.id


def _backoff(attempts: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=min(3600, 5 * (2 ** max(0, attempts))))


async def _already_processed(session: AsyncSession, event_id: uuid.UUID) -> bool:
    stmt = (
        pg_insert(ProcessedEvent)
        .values(consumer_id=CONSUMER_ID, event_id=event_id)
        .on_conflict_do_nothing(constraint="pk_processed_events")
        .returning(ProcessedEvent.event_id)
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none() is None


async def dispatch_batch(session: AsyncSession, *, batch: int = 100) -> dict[str, int]:
    """Claim and process pending outbox rows (FOR UPDATE SKIP LOCKED).

    Each event runs in its own SAVEPOINT: a failing event does not poison the
    batch, and successful events still commit.
    """
    claimed = (
        (
            await session.execute(
                text(
                    """
                    SELECT id FROM outbox_events
                    WHERE status = 'pending' AND next_attempt_at <= now()
                    ORDER BY created_at
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
    stats = {"processed": 0, "failed": 0, "skipped": 0}

    for event_id in claimed:
        row = (await session.execute(select(OutboxEvent).where(OutboxEvent.id == event_id))).scalar_one()
        # tenant GUC for this unit of work (RLS-ready workers, §82)
        if row.tenant_id is not None:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(row.tenant_id)},
            )
        try:
            async with session.begin_nested():
                if await _already_processed(session, event_id):
                    row.status = "processed"
                    row.processed_at = datetime.now(UTC)
                    stats["skipped"] += 1
                    continue
                handlers = EVENT_HANDLERS.get(row.event_name, [])
                envelope = {
                    "event_id": str(row.id),
                    "event_name": row.event_name,
                    "event_version": row.event_version,
                    "tenant_id": str(row.tenant_id) if row.tenant_id else None,
                    "aggregate_type": row.aggregate_type,
                    "aggregate_id": str(row.aggregate_id) if row.aggregate_id else None,
                    "occurred_at": row.occurred_at.isoformat(),
                    "trace_id": row.trace_id,
                    "causation_id": row.causation_id,
                    "payload": row.payload,
                }
                for handler in handlers:
                    await handler(session, envelope)
                row.status = "processed"
                row.processed_at = datetime.now(UTC)
                stats["processed"] += 1
        except Exception as exc:  # noqa: BLE001
            row.attempts += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            if row.attempts >= row.max_attempts:
                row.status = "dead"
                log.error("event %s (%s) dead-lettered: %s", row.id, row.event_name, row.last_error)
            else:
                row.status = "pending"
                row.next_attempt_at = _backoff(row.attempts)
                log.warning("event %s (%s) failed attempt %s: %s", row.id, row.event_name, row.attempts, exc)
            stats["failed"] += 1

    await session.flush()
    return stats
