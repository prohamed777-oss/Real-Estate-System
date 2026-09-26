"""Channel service: inbound pipeline (§7, §49) + outbound with consent gate (§5).

Inbound:  webhook → verify → persist raw → dedup → normalize → identity
          resolution → conversation → message → events (async processing)
Outbound: intent → consent/policy check → message row (queued) → event →
          job dispatches via adapter → status callbacks
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.gateway import InboundMessage, get_adapter
from app.channels.models import ChannelAccount, WebhookEvent
from app.conversations.models import Conversation
from app.conversations.service import (
    get_or_open_conversation,
    record_inbound_message,
    record_outbound_message,
    resolve_inbound_identity,
)
from app.core.audit import audit
from app.core.errors import NotFound, ValidationFailed
from app.events.queue import enqueue
from app.events.outbox import emit
from app.identity.models import CommunicationConsent, Identity, Person
from app.leads.service import create_lead
from app.organizations.models import Tenant


async def persist_webhook(
    session: AsyncSession, *, provider: str, channel: str, payload: dict[str, Any],
    signature: str | None = None, fingerprint: str | None = None, tenant_id: uuid.UUID | None = None,
) -> tuple[WebhookEvent, bool]:
    """Persist raw webhook, deduplicating by fingerprint (§49)."""
    if fingerprint is None:
        fingerprint = hashlib.sha256(
            repr(sorted(payload.items(), key=lambda kv: kv[0])).encode()
        ).hexdigest()
    existing = (
        await session.execute(
            select(WebhookEvent).where(WebhookEvent.fingerprint == fingerprint)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    event = WebhookEvent(
        tenant_id=tenant_id, provider=provider, channel=channel,
        external_signature=signature, fingerprint=fingerprint, payload=payload,
    )
    session.add(event)
    await session.flush()
    return event, True


async def _find_tenant_for_provider_ref(
    session: AsyncSession, *, provider: str, provider_ref: str | None
) -> uuid.UUID | None:
    """Resolve which tenant a webhook belongs to (provider account → tenant)."""
    if provider_ref:
        account = (
            await session.execute(
                select(ChannelAccount).where(
                    ChannelAccount.provider == provider,
                    ChannelAccount.config["external_ref"].astext == provider_ref,
                    ChannelAccount.status == "connected",
                )
            )
        ).scalar_one_or_none()
        if account:
            return account.tenant_id
    account = (
        await session.execute(
            select(ChannelAccount).where(
                ChannelAccount.provider == provider, ChannelAccount.status == "connected"
            )
        )
    ).scalar_one_or_none()
    return account.tenant_id if account else None


async def process_inbound_message(
    session: AsyncSession, *, tenant_id: uuid.UUID, msg: InboundMessage
) -> dict[str, Any]:
    """Full normalized inbound pipeline for ONE message."""
    person, person_new = await resolve_inbound_identity(
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
    if not message_new:
        return {"conversation_id": str(conversation.id), "message_id": str(message.id), "duplicate": True}

    # First contact on a channel with no active lead → acquisition entry (§8)
    lead_id: str | None = None
    if conv_new:
        from sqlalchemy import select as sel

        from app.leads.models import Lead

        open_lead = (
            await session.execute(
                sel(Lead).where(
                    Lead.tenant_id == tenant_id, Lead.person_id == person.id,
                    Lead.lifecycle_stage.notin_(("CONVERTED", "DISQUALIFIED")),
                )
            )
        ).scalar_one_or_none()
        if open_lead is None:
            lead = await create_lead(
                session, tenant_id=tenant_id, person_id=person.id,
                source=f"channel:{msg.channel}", created_by="webhook",
            )
            lead_id = str(lead.id)

    return {
        "conversation_id": str(conversation.id),
        "message_id": str(message.id),
        "person_id": str(person.id),
        "person_new": person_new,
        "conversation_new": conv_new,
        "lead_id": lead_id,
    }


async def send_outbound_message(
    session: AsyncSession, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    sender_type: str, sender_id: str | None, text: str | None,
    message_type: str = "text", media_url: str | None = None, caption: str | None = None,
    idempotency_ref: str | None = None, bypass_consent: bool = False,
) -> dict[str, Any]:
    """Outbound intent (§5, §7): consent gate happens HERE — before any provider.

    AI senders never bypass consent; only explicit operator action may set
    bypass_consent=True (e.g. replying to a direct customer question).
    """
    conversation = (
        await session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise NotFound("Conversation not found")
    if sender_type == "ai" and bypass_consent:
        raise ValidationFailed("AI senders cannot bypass the consent gate")

    if not bypass_consent:
        consent = (
            await session.execute(
                select(CommunicationConsent).where(
                    CommunicationConsent.tenant_id == tenant_id,
                    CommunicationConsent.person_id == conversation.person_id,
                    CommunicationConsent.channel == conversation.channel,
                )
            )
        ).scalars().all()
        do_not_contact = any(c.do_not_contact for c in consent)
        if do_not_contact:
            raise ValidationFailed("Person is marked do-not-contact")
        marketing = any(
            c for c in consent if c.consent_type == "marketing" and c.status == "opt_out"
        )
        is_transactional = sender_type in ("user", "system")  # direct replies = transactional
        if marketing and not is_transactional and sender_type == "ai":
            raise ValidationFailed("Marketing consent missing")

    msg = await record_outbound_message(
        session, conversation=conversation, sender_type=sender_type, sender_id=sender_id,
        message_type=message_type, text=text, attachments=[{"media_url": media_url}] if media_url else [],
        provider=conversation.provider, idempotency_ref=idempotency_ref,
    )
    await enqueue(
        session, job_type="channel.dispatch_message", tenant_id=tenant_id,
        payload={"message_id": str(msg.id)},
        unique_key=f"msg-{msg.id}",
    )
    return {"message_id": str(msg.id), "status": msg.status}


async def dispatch_message(session: AsyncSession, *, tenant_id: uuid.UUID, message_id: str) -> None:
    """Job: push a queued message through the tenant's adapter (§7 outbound flow)."""
    from app.conversations.models import Message

    msg = (
        await session.execute(
            select(Message).where(Message.id == uuid.UUID(message_id), Message.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if msg is None or msg.status not in ("queued", "failed"):
        return
    account = (
        await session.execute(
            select(ChannelAccount).where(
                ChannelAccount.tenant_id == tenant_id,
                ChannelAccount.channel == msg.channel,
                ChannelAccount.status == "connected",
            )
        )
    ).scalar_one_or_none()
    if account is None:
        # No connected provider yet → self-heal: retry in 30s (bounded by job max_attempts).
        await enqueue(
            session, job_type="channel.dispatch_message", tenant_id=tenant_id,
            payload={"message_id": str(msg.id)}, delay_seconds=30,
            unique_key=f"msg-{msg.id}",
        )
        return

    identity = (
        await session.execute(
            select(Conversation, Identity)
            .join(Identity, Identity.person_id == Conversation.person_id)
            .where(
                Conversation.id == msg.conversation_id,
                Identity.channel == Conversation.channel,
            )
        )
    ).first()
    if identity is None:
        msg.status = "failed"
        msg.error = "no channel identity for person"
        return
    _, ident = identity

    adapter = get_adapter(account.provider, account.config)
    try:
        if msg.message_type == "text" or not (msg.attachments and msg.attachments[0].get("media_url")):
            result = await adapter.send_text(ident.external_id, msg.text or "", idempotency_ref=msg.idempotency_ref)
        else:
            media_url = msg.attachments[0].get("media_url", "")
            result = await adapter.send_media(
                ident.external_id, media_url, caption=None, media_type="image",
                idempotency_ref=msg.idempotency_ref,
            )
    except Exception as exc:  # noqa: BLE001 — provider errors must not crash the worker
        msg.status = "failed"
        msg.error = str(exc)[:500]
        await emit(
            session, event_name="message.failed", tenant_id=tenant_id,
            aggregate_type="conversation", aggregate_id=msg.conversation_id,
            payload={"message_id": str(msg.id), "error": str(exc)[:300]},
        )
        return

    if result.status == "sent":
        msg.status = "sent"
        msg.external_id = result.external_id
        msg.sent_at = datetime.now(UTC)
        await emit(
            session, event_name="message.sent", tenant_id=tenant_id,
            aggregate_type="conversation", aggregate_id=msg.conversation_id,
            payload={"message_id": str(msg.id), "external_id": result.external_id},
        )
    else:
        msg.status = "failed"
        msg.error = result.error
        await emit(
            session, event_name="message.failed", tenant_id=tenant_id,
            aggregate_type="conversation", aggregate_id=msg.conversation_id,
            payload={"message_id": str(msg.id), "error": result.error},
        )


async def connect_channel_account(
    session: AsyncSession, *, tenant_id: uuid.UUID, channel: str, provider: str,
    config: dict[str, Any], display_name: str | None = None, actor_id: uuid.UUID | str | None = None,
) -> ChannelAccount:
    account = (
        await session.execute(
            select(ChannelAccount).where(
                ChannelAccount.tenant_id == tenant_id, ChannelAccount.channel == channel,
                ChannelAccount.provider == provider,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        account = ChannelAccount(tenant_id=tenant_id, channel=channel, provider=provider)
        session.add(account)
    account.config = config
    account.display_name = display_name
    account.status = "connected"
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="channel.connected", entity_type="channel_account", entity_id=account.id,
        after={"channel": channel, "provider": provider},
    )
    return account
