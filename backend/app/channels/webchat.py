"""Public Webchat API — website visitors chat with the AI Reception Agent.

No auth required. Visitors are identified by a session_id (browser-generated).
Flow:
    visitor message → session person → conversation → AI response → reply

Rate limited per session to prevent abuse.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.service import record_inbound_message, record_outbound_message
from app.identity.models import Identity, Person
from app.conversations.service import (
    get_or_open_conversation,
)
from app.core.db import get_session
from app.core.errors import NotFound
from app.core.ratelimit import check_rate_limit
from app.identity.models import Person
from app.leads.service import create_lead
from app.organizations.models import Tenant

router = APIRouter(prefix="/webchat", tags=["webchat"])

_sessions: dict[str, str] = {}  # session_id → tenant_id (in-memory cache)


class WebchatMessage(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    tenant_slug: str
    message: str = Field(min_length=1, max_length=2000)
    visitor_name: str | None = None
    visitor_phone: str | None = None


class WebchatResponse(BaseModel):
    response: str
    conversation_id: str
    session_id: str
    lead_captured: bool = False


@router.post("/message")
async def webchat_message(
    body: WebchatMessage,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Public endpoint: website visitors chat with the AI Reception Agent.
    Rate limited, session-scoped, tenant-isolated."""
    # rate limit per session (30 msg/min)
    from app.core.ratelimit import RateLimitExceeded

    try:
        await check_rate_limit(
            session, key=f"webchat:{body.session_id}", limit=30, window_seconds=60
        )
    except RateLimitExceeded:
        return {"response": "معلش استنى شوية — بعت رسايل كتير في وقت قصير. 🙏",
                 "conversation_id": "", "session_id": body.session_id}

    # resolve tenant
    tenant = (
        await session.execute(select(Tenant).where(Tenant.slug == body.tenant_slug))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFound(f"Tenant {body.tenant_slug!r} not found")

    # identity resolution (session-based, not phone-based)
    person, person_new = await _resolve_webchat_visitor(
        session, tenant_id=tenant.id, session_id=body.session_id,
        name=body.visitor_name,
    )
    conversation, conv_new = await get_or_open_conversation(
        session, tenant_id=tenant.id, person_id=person.id,
        channel="webchat", provider="webchat",
    )
    msg, _ = await record_inbound_message(
        session, conversation=conversation, external_id=None,
        message_type="text", text=body.message, attachments=[],
        provider="webchat",
    )

    # create lead if new conversation (same as WhatsApp flow)
    lead_id = None
    if conv_new:
        from sqlalchemy import select as sel

        from app.leads.models import Lead

        open_lead = (
            await session.execute(
                sel(Lead).where(
                    Lead.tenant_id == tenant.id, Lead.person_id == person.id,
                    Lead.lifecycle_stage.notin_(("CONVERTED", "DISQUALIFIED")),
                )
            )
        ).scalar_one_or_none()
        if open_lead is None:
            lead = await create_lead(
                session, tenant_id=tenant.id, person_id=person.id,
                source="webchat", created_by="webchat",
            )
            lead_id = str(lead.id)

    # run AI Reception Agent
    from app.ai.runtime import execute_agent

    profile = await _get_reception_profile(session, tenant.id)
    result = await execute_agent(
        session, tenant_id=tenant.id, profile=profile,
        user_message=body.message, conversation_id=conversation.id,
        person_id=person.id,
        permissions={"people:read", "properties:read", "leads:read", "leads:write",
                      "conversations:read", "conversations:write"},
    )

    # record AI response as outbound message
    if result.message:
        await record_outbound_message(
            session, conversation=conversation, sender_type="ai",
            sender_id="agent:reception", message_type="text",
            text=result.message, attachments=[], provider="webchat",
        )

    return {
        "response": result.message,
        "conversation_id": str(conversation.id),
        "session_id": body.session_id,
        "lead_captured": lead_id is not None,
    }


async def _resolve_webchat_visitor(
    session: AsyncSession, *, tenant_id: uuid.UUID, session_id: str,
    name: str | None = None,
) -> tuple[Person, bool]:
    """Find or create a visitor person by webchat session_id."""
    from app.identity.models import Identity

    identity = (
        await session.execute(
            select(Identity).where(
                Identity.tenant_id == tenant_id,
                Identity.channel == "webchat",
                Identity.external_id == session_id,
            )
        )
    ).scalar_one_or_none()
    if identity is not None:
        person = await session.get(Person, identity.person_id)
        if person:
            return person, False
    person = Person(
        tenant_id=tenant_id, type="customer",
        full_name=name or f"زائر {session_id[:6]}",
        locale="ar", source="webchat",
    )
    session.add(person)
    await session.flush()
    session.add(Identity(
        tenant_id=tenant_id, person_id=person.id,
        channel="webchat", external_id=session_id,
        display_name=name,
    ))
    await session.flush()
    return person, True


async def _get_reception_profile(session: AsyncSession, tenant_id: uuid.UUID):
    from app.ai.models import AgentProfile
    row = (
        await session.execute(
            select(AgentProfile)
            .where(
                AgentProfile.tenant_id == tenant_id,
                AgentProfile.key == "reception",
                AgentProfile.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row:
        return row
    row = (
        await session.execute(
            select(AgentProfile)
            .where(
                AgentProfile.tenant_id.is_(None),
                AgentProfile.key == "reception",
                AgentProfile.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Reception agent profile not found")
    return row


# capture visitor contact info → upgrade to a real lead
class WebchatCapture(BaseModel):
    session_id: str
    tenant_slug: str
    name: str
    phone: str
    email: str | None = None


@router.post("/capture")
async def capture_visitor(
    body: WebchatCapture, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Visitor provides contact info → upgrade session visitor to a real person."""
    tenant = (
        await session.execute(
            select(Tenant).where(Tenant.slug == body.tenant_slug)
        )
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFound("Tenant not found")

    identity = (
        await session.execute(
            select(Identity).where(
                Identity.tenant_id == tenant.id,
                Identity.channel == "webchat",
                Identity.external_id == body.session_id,
            )
        )
    ).scalar_one_or_none()
    if identity is None:
        raise NotFound("Session not found")

    person = await session.get(Person, identity.person_id)
    person.full_name = body.name
    person.phone = body.phone
    person.email = body.email
    await session.flush()

    # create lead if none exists
    from app.leads.models import Lead

    lead = (
        await session.execute(
            select(Lead).where(
                Lead.tenant_id == tenant.id, Lead.person_id == person.id,
                Lead.lifecycle_stage.notin_(("CONVERTED", "DISQUALIFIED")),
            )
        )
    ).scalar_one_or_none()
    if lead is None:
        await create_lead(
            session, tenant_id=tenant.id, person_id=person.id,
            source="webchat_capture", created_by="webchat",
        )

    return {"person_id": str(person.id), "name": person.full_name,
             "phone": person.phone, "captured": True}
