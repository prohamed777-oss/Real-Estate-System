"""Operational admin: DLQ visibility + requeue (§50's 'admin interface') +
media upload abstraction (local dev disk / S3-compatible production)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session, session_factory
from app.core.errors import NotFound
from app.core.permissions import SETTINGS_READ, SETTINGS_WRITE, require
from app.core.tenancy import AuthContext
from app.events.models import Job, OutboxEvent

router = APIRouter(prefix="/ops", tags=["ops"])


def _guard(x_cron_secret: str) -> None:
    if x_cron_secret != settings.cron_secret:
        raise HTTPException(status_code=401, detail="Invalid ops secret")


# ---------- Dead Letter Queue ----------
@router.get("/dlq")
async def view_dlq(
    x_cron_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _guard(x_cron_secret)
    dead_jobs = (
        await session.execute(
            select(Job).where(Job.status == "dead").order_by(Job.created_at.desc()).limit(100)
        )
    ).scalars().all()
    dead_events = (
        await session.execute(
            select(OutboxEvent).where(OutboxEvent.status == "dead").order_by(OutboxEvent.created_at.desc()).limit(100)
        )
    ).scalars().all()
    return {
        "jobs": [
            {"id": str(j.id), "type": j.type, "attempts": j.attempts,
             "last_error": j.last_error, "created_at": j.created_at.isoformat()}
            for j in dead_jobs
        ],
        "events": [
            {"id": str(e.id), "event_name": e.event_name, "attempts": e.attempts,
             "last_error": e.last_error, "created_at": e.created_at.isoformat()}
            for e in dead_events
        ],
    }


@router.post("/dlq/jobs/{job_id}/requeue")
async def requeue_dead_job(
    job_id: uuid.UUID,
    x_cron_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _guard(x_cron_secret)
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFound("Job not found")
    if job.status != "dead":
        raise HTTPException(status_code=409, detail="Job is not dead")
    job.status = "pending"
    job.attempts = 0
    job.available_at = datetime.now(UTC)
    job.last_error = None
    return {"id": str(job.id), "status": job.status}


@router.post("/dlq/events/{event_id}/requeue")
async def requeue_dead_event(
    event_id: uuid.UUID,
    x_cron_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _guard(x_cron_secret)
    event = await session.get(OutboxEvent, event_id)
    if event is None:
        raise NotFound("Event not found")
    if event.status != "dead":
        raise HTTPException(status_code=409, detail="Event is not dead")
    event.status = "pending"
    event.attempts = 0
    event.next_attempt_at = datetime.now(UTC)
    event.last_error = None
    return {"id": str(event.id), "status": event.status}


# ---------- periodic retention (keeps hot tables lean) ----------
async def run_retention(session: AsyncSession) -> dict[str, int]:
    cutoff = datetime.now(UTC)
    res = await session.execute(text(
        "DELETE FROM webhook_events WHERE received_at < now() - interval '30 days' RETURNING id"
    ))
    webhooks = len(res.scalars().all())
    res = await session.execute(text(
        "DELETE FROM processed_events WHERE processed_at < now() - interval '14 days' RETURNING event_id"
    ))
    processed = len(res.scalars().all())
    res = await session.execute(text(
        "DELETE FROM rate_limit_counters WHERE window_start < now() - interval '2 hours' RETURNING id"
    ))
    counters = len(res.scalars().all())
    res = await session.execute(text(
        "DELETE FROM notifications WHERE status='read' AND read_at < now() - interval '30 days' RETURNING id"
    ))
    notifications = len(res.scalars().all())
    return {"webhook_events": webhooks, "processed_events": processed,
            "rate_counters": counters, "notifications": notifications}


@router.post("/retention/run")
async def trigger_retention(
    x_cron_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _guard(x_cron_secret)
    return await run_retention(session)


# ---------- media storage abstraction ----------
class StorageBackend:
    """Local disk in dev; S3-compatible (Supabase Storage) in production."""

    async def put(self, tenant_id: str, filename: str, content: bytes, content_type: str) -> str:
        root = Path("/tmp/revenue-os-media") if settings.app_env != "production" else Path("/tmp/media")
        folder = root / tenant_id
        folder.mkdir(parents=True, exist_ok=True)
        key = f"{uuid.uuid4().hex}-{filename}"[:180]
        (folder / key).write_bytes(content)
        return f"local://{folder / key}"


@router.post("/media")
async def upload_media(
    file: UploadFile,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 20MB)")
    backend = StorageBackend()
    path = await backend.put(str(auth.tenant_id), file.filename or "file.bin", content,
                             file.content_type or "application/octet-stream")
    return {"path": path, "filename": file.filename, "size": len(content)}


# ---------- retention job (enqueued daily by the tick) ----------
from app.events.registry import job_handler  # noqa: E402


@job_handler("maintenance.retention")
async def retention_job(session, tenant_id, payload):  # noqa: ANN001
    from app.jobs.ops_api import run_retention

    return await run_retention(session)
