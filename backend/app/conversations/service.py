"""Conversation services: unified inbox operations (§6, §69)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import Conversation, ConversationAssignment, Message
from app.core.errors import NotFound
from app.events.outbox import emit
from app.identity.models import Identity, Person


async def resolve_inbound_identity(
    session: AsyncSession, *, tenant_id: uuid.UUID, channel: str, external_id: str,
    display_name: str | None = None,
) -> tuple[Person, bool]:
    """Identity resolution (§4, §7): find the person behind a channel handle,
    creating person+identity when new. Never creates duplicate persons for
    known handles."""
    identity = (
        await session.execute(
            select(Identity).where(
                Identity.tenant_id == tenant_id,
                Identity.channel == channel,
                Identity.external_id == external_id,
            )
        )
    ).scalar_one_or_none()
    if identity is not None:
        identity.last_seen_at = datetime.now(UTC)
        if display_name and not identity.display_name:
            identity.display_name = display_name
        person = await session.get(Person, identity.person_id)
        return person, False

    person = Person(
        tenant_id=tenant_id,
        type="customer",
        full_name=display_name or "",
        locale="ar",
        source=channel,
    )
    session.add(person)
    await session.flush()
    session.add(
        Identity(
            tenant_id=tenant_id,
            person_id=person.id,
            channel=channel,
            external_id=external_id,
            display_name=display_name,
            last_seen_at=datetime.now(UTC),
        )
    )
    await emit(
        session, event_name="person.created", tenant_id=tenant_id, aggregate_type="person",
        aggregate_id=person.id, payload={"person_id": str(person.id), "channel": channel,
                                          "external_id": external_id, "new": True},
    )
    return person, True


async def get_or_open_conversation(
    session: AsyncSession, *, tenant_id: uuid.UUID, person_id: uuid.UUID, channel: str,
    provider: str | None = None,
) -> tuple[Conversation, bool]:
    conv = (
        await session.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.person_id == person_id,
                Conversation.channel == channel,
                Conversation.status.in_(("open", "pending")),
            )
        )
    ).scalar_one_or_none()
    if conv is not None:
        return conv, False
    conv = Conversation(tenant_id=tenant_id, person_id=person_id, channel=channel, provider=provider)
    session.add(conv)
    await session.flush()
    await emit(
        session, event_name="conversation.created", tenant_id=tenant_id, aggregate_type="conversation",
        aggregate_id=conv.id,
        payload={"conversation_id": str(conv.id), "person_id": str(person_id), "channel": channel},
    )
    return conv, True


async def record_inbound_message(
    session: AsyncSession, *, conversation: Conversation, external_id: str | None,
    message_type: str, text: str | None, attachments: list | None, provider: str | None,
) -> tuple[Message, bool]:
    """Deduplicate provider retries by external_id (§7 webhook dedup)."""
    if external_id:
        existing = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == conversation.id, Message.external_id == external_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
    msg = Message(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        direction="inbound",
        sender_type="customer",
        sender_id=str(conversation.person_id),
        channel=conversation.channel,
        provider=provider,
        external_id=external_id,
        message_type=message_type,
        text=text,
        attachments=attachments or [],
        status="received",
    )
    session.add(msg)
    conversation.unread_count += 1
    conversation.last_message_at = datetime.now(UTC)
    if text:
        conversation.last_message_preview = text[:300]
    await session.flush()
    await emit(
        session, event_name="message.received", tenant_id=conversation.tenant_id,
        aggregate_type="conversation", aggregate_id=conversation.id,
        payload={"message_id": str(msg.id), "conversation_id": str(conversation.id),
                 "person_id": str(conversation.person_id), "channel": conversation.channel,
                 "text": text, "message_type": message_type},
    )
    return msg, True


async def record_outbound_message(
    session: AsyncSession, *, conversation: Conversation, sender_type: str, sender_id: str | None,
    message_type: str, text: str | None, attachments: list | None, provider: str | None,
    idempotency_ref: str | None = None,
) -> Message:
    msg = Message(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        direction="outbound",
        sender_type=sender_type,
        sender_id=sender_id,
        channel=conversation.channel,
        provider=provider,
        message_type=message_type,
        text=text,
        attachments=attachments or [],
        status="queued",
        idempotency_ref=idempotency_ref,
    )
    session.add(msg)
    conversation.last_message_at = datetime.now(UTC)
    if text:
        conversation.last_message_preview = text[:300]
    await session.flush()
    await emit(
        session, event_name="message.queued", tenant_id=conversation.tenant_id,
        aggregate_type="conversation", aggregate_id=conversation.id,
        payload={"message_id": str(msg.id), "conversation_id": str(conversation.id),
                 "channel": conversation.channel, "text": text,
                 "sender_type": sender_type, "idempotency_ref": idempotency_ref},
    )
    return msg


async def assign_conversation(
    session: AsyncSession, *, conversation: Conversation, assigned_to: uuid.UUID,
    assigned_by: str | None = None, reason: str | None = None,
) -> Conversation:
    if conversation.assigned_user_id:
        previous = (
            await session.execute(
                select(ConversationAssignment).where(
                    ConversationAssignment.conversation_id == conversation.id,
                    ConversationAssignment.released_at.is_(None),
                )
            )
        ).scalars().all()
        for p in previous:
            p.released_at = datetime.now(UTC)
    session.add(
        ConversationAssignment(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            assigned_to=assigned_to,
            assigned_by=assigned_by,
            reason=reason,
        )
    )
    conversation.assigned_user_id = assigned_to
    await session.flush()
    await emit(
        session, event_name="conversation.assigned", tenant_id=conversation.tenant_id,
        aggregate_type="conversation", aggregate_id=conversation.id,
        payload={"conversation_id": str(conversation.id), "assigned_to": str(assigned_to),
                 "reason": reason},
    )
    return conversation


async def get_conversation(session: AsyncSession, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    conv = (
        await session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        raise NotFound("Conversation not found")
    return conv
