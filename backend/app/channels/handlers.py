"""Channel event/job handlers + conversation → lead activity signals."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from app.channels.gateway import InboundMessage
from app.channels.service import dispatch_message
from app.conversations.service import record_inbound_message
from app.events.queue import enqueue
from app.events.registry import event_handler, job_handler

log = logging.getLogger("revenue_os.channels")


@job_handler("channel.process_webhook")
async def process_webhook_job(session, tenant_id, payload):  # noqa: ANN001
    """Process normalized webhook messages: conversation + message + lead."""
    from datetime import UTC, datetime

    from app.channels.models import WebhookEvent
    from app.conversations.service import get_or_open_conversation, resolve_inbound_identity

    webhook_id = payload.get("webhook_id")
    messages = payload.get("messages", [])
    for m in messages:
        msg = InboundMessage(
            channel=m.get("channel", "whatsapp"),
            provider=m.get("provider", "meta_whatsapp"),
            external_id=m.get("external_id"),
            person_ref=m["person_ref"],
            person_name=m.get("person_name"),
            message_type=m.get("message_type", "text"),
            text=m.get("text"),
            attachments=m.get("attachments", []),
        )
        person, _ = await resolve_inbound_identity(
            session, tenant_id=tenant_id, channel=msg.channel,
            external_id=msg.person_ref, display_name=msg.person_name,
        )
        conversation, conv_new = await get_or_open_conversation(
            session, tenant_id=tenant_id, person_id=person.id, channel=msg.channel,
            provider=msg.provider,
        )
        message, message_new = await record_inbound_message(
            session, conversation=conversation, external_id=msg.external_id,
            message_type=msg.message_type, text=msg.text, attachments=msg.attachments,
            provider=msg.provider,
        )
        if message_new and conv_new:
            from sqlalchemy import select as sel

            from app.leads.models import Lead
            from app.leads.service import create_lead

            open_lead = (
                await session.execute(
                    sel(Lead).where(
                        Lead.tenant_id == tenant_id, Lead.person_id == person.id,
                        Lead.lifecycle_stage.notin_(("CONVERTED", "DISQUALIFIED")),
                    )
                )
            ).scalar_one_or_none()
            if open_lead is None:
                await create_lead(
                    session, tenant_id=tenant_id, person_id=person.id,
                    source=f"channel:{msg.channel}", created_by="webhook",
                )
    if webhook_id:
        webhook = await session.get(WebhookEvent, uuid.UUID(webhook_id))
        if webhook:
            webhook.status = "processed"
            webhook.processed_at = datetime.now(UTC)


@event_handler("message.queued")
async def on_message_queued(session, envelope):  # noqa: ANN001
    """Trigger provider dispatch as a job (keeps webhook/API paths fast)."""
    payload = envelope["payload"]
    tenant_id = uuid.UUID(envelope["tenant_id"]) if envelope.get("tenant_id") else None
    await enqueue(
        session, job_type="channel.dispatch_message", tenant_id=tenant_id,
        payload={"message_id": payload["message_id"]},
    )


@job_handler("channel.dispatch_message")
async def dispatch_message_job(session, tenant_id, payload):  # noqa: ANN001
    if tenant_id is None:
        return
    await dispatch_message(session, tenant_id=tenant_id, message_id=payload["message_id"])


@event_handler("message.received")
async def on_message_received(session, envelope):  # noqa: ANN001
    """Update lead engagement signals from message activity (scoring input)."""
    payload = envelope["payload"]
    conversation_id = payload.get("conversation_id")
    if not conversation_id or not envelope.get("tenant_id"):
        return

    from app.conversations.models import Conversation
    from app.leads.models import Lead

    tenant_id = uuid.UUID(envelope["tenant_id"])
    conv = (
        await session.execute(
            select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
        )
    ).scalar_one_or_none()
    if conv is None:
        return
    lead = (
        await session.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_id, Lead.person_id == conv.person_id,
                Lead.lifecycle_stage.notin_(("CONVERTED", "DISQUALIFIED")),
            )
        )
    ).scalar_one_or_none()
    if lead is None:
        return
    signals = dict(lead.score_signals or {})
    activity = dict(signals.get("activity", {}))
    messages_7d = activity.get("messages_last_7d", 0) + 1
    activity["messages_last_7d"] = messages_7d
    signals["activity"] = activity
    lead.score_signals = signals
    lead.lifecycle_stage = (
        lead.lifecycle_stage  # unchanged; scoring signal only
    )
    await session.flush()
