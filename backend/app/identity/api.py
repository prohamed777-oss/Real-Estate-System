"""People / identities / consents API (§4-5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.db import get_session
from app.core.errors import NotFound, ValidationFailed
from app.core.pagination import CursorPage, encode_cursor
from app.core.permissions import PEOPLE_READ, PEOPLE_WRITE, require
from app.core.tenancy import AuthContext
from app.identity.models import (
    CHANNELS,
    CommunicationConsent,
    CustomerProfile,
    Identity,
    Person,
)

router = APIRouter(prefix="/people", tags=["people"])


class PersonIn(BaseModel):
    type: str = "customer"
    full_name: str = ""
    full_name_alt: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    company_name: str | None = None
    locale: str = "ar"
    timezone: str | None = None
    notes: str | None = None
    tags: list[str] = []


class IdentityIn(BaseModel):
    channel: str
    external_id: str
    display_name: str | None = None


class ConsentIn(BaseModel):
    channel: str
    consent_type: str = "marketing"
    status: str
    source: str | None = None
    do_not_contact: bool = False


def _person_dict(p: Person) -> dict[str, Any]:
    return {
        "id": str(p.id), "type": p.type, "full_name": p.full_name, "full_name_alt": p.full_name_alt,
        "email": p.email, "phone": p.phone, "company_name": p.company_name, "locale": p.locale,
        "timezone": p.timezone, "notes": p.notes, "tags": p.tags, "status": p.status,
        "created_at": p.created_at.isoformat(),
    }


@router.get("")
async def list_people(
    auth: AuthContext = Depends(require(PEOPLE_READ)),
    session: AsyncSession = Depends(get_session),
    q: str | None = None,
    type_: str | None = Query(default=None, alias="type"),
    tag: str | None = None,
    limit: int = Query(default=50, le=200),
    cursor: str | None = None,
) -> CursorPage:
    query = select(Person).where(Person.tenant_id == auth.tenant_id, Person.status == "active")
    if q:
        like = f"%{q.lower()}%"
        query = query.where(
            or_(
                func.lower(Person.full_name).like(like),
                func.lower(func.coalesce(Person.email, "")).like(like),
                func.coalesce(Person.phone, "").like(like),
            )
        )
    if type_:
        query = query.where(Person.type == type_)
    if tag:
        query = query.where(Person.tags.contains([tag]))
    query = query.order_by(Person.created_at.desc(), Person.id.desc())
    rows = (await session.execute(query.limit(limit))).scalars().all()
    items = [_person_dict(p) for p in rows]
    next_cursor = encode_cursor(created_at=rows[-1].created_at, id_=rows[-1].id) if len(items) == limit and rows else None
    return CursorPage(items=items, next_cursor=next_cursor, has_more=bool(next_cursor))


@router.post("", status_code=201)
async def create_person(
    body: PersonIn,
    auth: AuthContext = Depends(require(PEOPLE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    person = Person(tenant_id=auth.tenant_id, **body.model_dump())
    session.add(person)
    await session.flush()
    session.add(CustomerProfile(tenant_id=auth.tenant_id, person_id=person.id, language=body.locale))
    await audit(session, tenant_id=auth.tenant_id, actor_type="user", actor_id=auth.user_id,
                action="person.created", entity_type="person", entity_id=person.id,
                after={"full_name": person.full_name, "email": person.email})
    return _person_dict(person)


@router.get("/{person_id}")
async def get_person(
    person_id: uuid.UUID,
    auth: AuthContext = Depends(require(PEOPLE_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    person = (
        await session.execute(
            select(Person).where(Person.id == person_id, Person.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if person is None:
        raise NotFound("Person not found")
    identities = (
        await session.execute(select(Identity).where(Identity.person_id == person_id))
    ).scalars().all()
    consent_rows = (
        await session.execute(select(CommunicationConsent).where(CommunicationConsent.person_id == person_id))
    ).scalars().all()
    profile = (
        await session.execute(select(CustomerProfile).where(CustomerProfile.person_id == person_id))
    ).scalar_one_or_none()
    return {
        **_person_dict(person),
        "identities": [
            {"id": str(i.id), "channel": i.channel, "external_id": i.external_id,
             "display_name": i.display_name, "last_seen_at": i.last_seen_at.isoformat() if i.last_seen_at else None}
            for i in identities
        ],
        "consents": [
            {"channel": c.channel, "consent_type": c.consent_type, "status": c.status,
             "do_not_contact": c.do_not_contact}
            for c in consent_rows
        ],
        "profile": {
            "preferred_channel": profile.preferred_channel if profile else None,
            "language": profile.language if profile else "ar",
            "explicit_preferences": profile.explicit_preferences if profile else {},
            "behavioral_preferences": profile.behavioral_preferences if profile else {},
        },
    }


@router.patch("/{person_id}")
async def update_person(
    person_id: uuid.UUID,
    body: PersonIn,
    auth: AuthContext = Depends(require(PEOPLE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    person = (
        await session.execute(
            select(Person).where(Person.id == person_id, Person.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if person is None:
        raise NotFound("Person not found")
    before = {"full_name": person.full_name, "email": person.email, "phone": person.phone, "tags": person.tags}
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(person, field, value)
    await audit(session, tenant_id=auth.tenant_id, actor_type="user", actor_id=auth.user_id,
                action="person.updated", entity_type="person", entity_id=person.id,
                before=before, after=data)
    return _person_dict(person)


@router.post("/{person_id}/identities", status_code=201)
async def add_identity(
    person_id: uuid.UUID,
    body: IdentityIn,
    auth: AuthContext = Depends(require(PEOPLE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if body.channel not in CHANNELS:
        raise ValidationFailed(f"Unknown channel: {body.channel}")
    person = (
        await session.execute(
            select(Person).where(Person.id == person_id, Person.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if person is None:
        raise NotFound("Person not found")
    identity = Identity(
        tenant_id=auth.tenant_id, person_id=person_id, channel=body.channel,
        external_id=body.external_id, display_name=body.display_name,
        last_seen_at=datetime.now(UTC),
    )
    session.add(identity)
    await session.flush()
    return {"id": str(identity.id), "channel": identity.channel, "external_id": identity.external_id}


@router.put("/{person_id}/consents")
async def set_consent(
    person_id: uuid.UUID,
    body: ConsentIn,
    auth: AuthContext = Depends(require(PEOPLE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if body.channel not in CHANNELS or body.status not in {"opt_in", "opt_out"}:
        raise ValidationFailed("Invalid channel or consent status")
    consent = (
        await session.execute(
            select(CommunicationConsent).where(
                CommunicationConsent.tenant_id == auth.tenant_id,
                CommunicationConsent.person_id == person_id,
                CommunicationConsent.channel == body.channel,
                CommunicationConsent.consent_type == body.consent_type,
            )
        )
    ).scalar_one_or_none()
    if consent is None:
        consent = CommunicationConsent(tenant_id=auth.tenant_id, person_id=person_id, **body.model_dump())
        session.add(consent)
    else:
        consent.status = body.status
        consent.do_not_contact = body.do_not_contact
        consent.source = body.source or consent.source
    await audit(session, tenant_id=auth.tenant_id, actor_type="user", actor_id=auth.user_id,
                action="consent.changed", entity_type="person", entity_id=person_id,
                after=body.model_dump())
    return {"status": consent.status, "do_not_contact": consent.do_not_contact}
