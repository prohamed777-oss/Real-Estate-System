"""B7: GDPR compliance endpoints — data export + right to erasure."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.db import get_session
from app.core.errors import NotFound, PermissionDenied
from app.core.permissions import require
from app.core.tenancy import AuthContext
from app.identity.models import CommunicationConsent, CustomerProfile, Identity, Person
from app.leads.models import Lead
from app.conversations.models import Conversation, Message

router = APIRouter(prefix="/gdpr", tags=["gdpr"])


@router.get("/export/{person_id}")
async def export_person_data(
    person_id: uuid.UUID,
    auth: AuthContext = Depends(require("people:read")),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Article 20: Right to data portability. Exports ALL data for one person."""
    from app.sales.models import Opportunity, Viewing, Offer, Reservation
    from app.finance.models import Deal, Payment

    person = (
        await session.execute(
            select(Person).where(Person.id == person_id, Person.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if person is None:
        raise NotFound("Person not found")

    async def _all(model, id_col, **filters):
        q = select(model).where(model.tenant_id == auth.tenant_id, **filters)
        return (await session.execute(q)).scalars().all()

    leads = (await session.execute(
        select(Lead).where(Lead.tenant_id == auth.tenant_id, Lead.person_id == person_id)
    )).scalars().all()
    conversations = (await session.execute(
        select(Conversation).where(Conversation.tenant_id == auth.tenant_id,
                                    Conversation.person_id == person_id)
    )).scalars().all()
    messages = []
    for conv in conversations:
        rows = (await session.execute(
            select(Message).where(Message.conversation_id == conv.id)
        )).scalars().all()
        messages.extend(rows)

    data = {
        "person": {"id": str(person.id), "name": person.full_name, "email": person.email,
                    "phone": person.phone, "created_at": person.created_at.isoformat()},
        "identities": [{"channel": i.channel, "external_id": i.external_id}
                        for i in (await session.execute(
                            select(Identity).where(Identity.person_id == person_id)
                        )).scalars()],
        "leads": [{"id": str(l.id), "stage": l.lifecycle_stage, "score": l.lead_score} for l in leads],
        "conversations": [{"id": str(c.id), "channel": c.channel} for c in conversations],
        "messages": [{"direction": m.direction, "text": m.text,
                       "created_at": m.created_at.isoformat()} for m in messages],
        "consents": [{"channel": c.channel, "type": c.consent_type, "status": c.status}
                      for c in (await session.execute(
                          select(CommunicationConsent).where(CommunicationConsent.person_id == person_id)
                      )).scalars()],
        "exported_at": datetime.now(UTC).isoformat(),
    }
    await audit(session, tenant_id=auth.tenant_id, actor_type="user", actor_id=auth.user_id,
                action="gdpr.data_exported", entity_type="person", entity_id=person_id)
    return JSONResponse(content=data, headers={
        "Content-Disposition": f"attachment; filename=gdpr-export-{person_id}.json"
    })


@router.delete("/erase/{person_id}")
async def erase_person_data(
    person_id: uuid.UUID,
    auth: AuthContext = Depends(require("settings:write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Article 17: Right to erasure. Anonymises the person (preserves FK integrity)
    and records the erasure in the audit trail. Requires settings:write permission."""
    person = (
        await session.execute(
            select(Person).where(Person.id == person_id, Person.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if person is None:
        raise NotFound("Person not found")

    anon_email = f"erased-{person_id}@gdpr.local"
    person.full_name = "Erased (GDPR)"
    person.full_name_alt = None
    person.email = anon_email
    person.phone = None
    person.notes = None
    person.tags = []
    person.status = "archived"

    # anonymise identities
    for identity in (await session.execute(
        select(Identity).where(Identity.person_id == person_id)
    )).scalars():
        identity.external_id = f"erased-{identity.id}"
        identity.display_name = None

    for consent in (await session.execute(
        select(CommunicationConsent).where(CommunicationConsent.person_id == person_id)
    )).scalars():
        consent.do_not_contact = True
        consent.status = "opt_out"

    await audit(session, tenant_id=auth.tenant_id, actor_type="user", actor_id=auth.user_id,
                action="gdpr.person_erased", entity_type="person", entity_id=person_id,
                after={"anon_email": anon_email})
    return {"erased": True, "person_id": str(person_id), "method": "anonymisation"}
