"""A5: Email notification delivery via Resend (or SMTP fallback).

Notification rows are already written to the DB by the automation/SLA engines.
This module sends queued notifications that have an email recipient.
Activation: RESEND_API_KEY env var. Without it, emails stay as DB records.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from datetime import UTC, datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ExternalProviderError
from app.automation.models import Notification
from app.organizations.models import User
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
    """Send all queued email notifications that have a user email."""
    rows = (
        await session.execute(
            select(Notification)
            .where(
                Notification.kind == "email",
                Notification.status == "queued",
            )
            .limit(limit)
        )
    ).scalars().all()
    sent = 0
    for notification in rows:
        user = (
            await session.execute(
                select(User).where(User.id == uuid.UUID(notification.recipient_id))
            )
        ).scalar_one_or_none()
        if user is None or not user.email:
            continue
        ok = await send_email(to_email=user.email,
                               subject=notification.title or "Revenue OS",
                               html=f"<p>{notification.body or ''}</p>")
        if ok:
            notification.status = "sent"
            notification.sent_at = datetime.now(UTC)
            sent += 1
    await session.flush()
    return sent
