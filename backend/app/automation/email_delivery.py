"""A5: Email notification delivery via Resend (or SMTP fallback).

Notification rows are already written to the DB by the automation/SLA engines.
This module sends queued notifications that have an email recipient.
Activation: RESEND_API_KEY env var. Without it, emails stay as DB records.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.models import Notification
from app.core.config import settings
from app.organizations.models import User

log = logging.getLogger("revenue_os.email")

RESEND_API = "https://api.resend.com/emails"


async def send_email(
    *, to_email: str, subject: str, html: str,
    from_email: str = "notifications@revenueos.app",
) -> bool:
    """Send via Resend API. Returns True on success."""
    key = getattr(settings, "resend_api_key", "") or ""
    if not key:
        log.warning("RESEND_API_KEY not set — email skipped for %s", to_email)
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                RESEND_API,
                json={"from": from_email, "to": [to_email],
                       "subject": subject, "html": html},
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code >= 400:
            log.error("Resend error %d: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except httpx.HTTPError as exc:
        log.error("Resend unreachable: %s", exc)
        return False


async def deliver_queued_notifications(session: AsyncSession, *, limit: int = 20) -> int:
    """Send all queued email notifications that have a user email.

    FOR UPDATE SKIP LOCKED claims rows so two overlapping ticks cannot
    double-send; rows with a malformed/unknown recipient are marked failed so
    one poison record cannot block the queue head-of-line forever.
    """
    rows = (
        await session.execute(
            select(Notification)
            .where(
                Notification.kind == "email",
                Notification.status == "queued",
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    sent = 0
    for notification in rows:
        try:
            user = (
                await session.execute(
                    select(User).where(User.id == uuid.UUID(notification.recipient_id))
                )
            ).scalar_one_or_none()
        except (TypeError, ValueError):
            # recipient_id is free text populated from rule JSON — a malformed
            # value must not poison-pill the whole tick (V4 audit)
            notification.status = "failed"
            log.warning("notification %s: malformed recipient_id %r",
                        notification.id, notification.recipient_id)
            continue
        if user is None or not user.email:
            notification.status = "failed"
            log.warning("notification %s: no user/email for recipient %r — failed",
                        notification.id, notification.recipient_id)
            continue
        try:
            ok = await send_email(to_email=user.email,
                                   subject=notification.title or "Revenue OS",
                                   html=f"<p>{notification.body or ''}</p>")
        except Exception as exc:  # noqa: BLE001 — one bad row must not kill the tick
            log.error("notification %s: send raised %s: %s",
                      notification.id, type(exc).__name__, exc)
            ok = False
        if ok:
            notification.status = "sent"
            notification.sent_at = datetime.now(UTC)
            sent += 1
    await session.flush()
    return sent
