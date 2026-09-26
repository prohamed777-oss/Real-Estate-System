"""Person dedup & merge — the real-world CRM necessity.

Same customer arriving on WhatsApp then a phone call must remain ONE person.
find_duplicates: same normalized phone/email across identities.
merge_persons: moves identities, leads, conversations, tasks, opportunities to
the survivor, marks the other as merged (§4 never loses history).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.events.outbox import emit
from app.identity.models import CommunicationConsent, CustomerProfile, Identity, Person
from app.sales.models import Opportunity


def _normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits or None


async def find_duplicates(session: AsyncSession, *, tenant_id: uuid.UUID,
                          limit: int = 50) -> list[dict[str, Any]]:
    """Group active persons by normalized phone or email with >1 members."""
    people = (
        await session.execute(
            select(Person).where(Person.tenant_id == tenant_id, Person.status == "active")
        )
    ).scalars().all()
    groups: dict[str, list[Person]] = {}
    for p in people:
        for key in (f"phone:{_normalize_phone(p.phone)}", f"email:{(p.email or '').lower()}"):
            if key.endswith(":None") or key in (":", "email:"):
                continue
            groups.setdefault(key, []).append(p)
    seen: set[uuid.UUID] = set()
    out: list[dict[str, Any]] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        cluster = sorted({m.id for m in members})
        if cluster[0] in seen:
            continue
        seen.update(cluster)
        out.append({
            "keys": key.split(":")[0],
            "person_ids": [str(pid) for pid in cluster],
            "names": [m.full_name for m in members],
        })
        if len(out) >= limit:
            break
    return out


async def merge_persons(
    session: AsyncSession, *, tenant_id: uuid.UUID, primary_id: uuid.UUID,
    duplicate_id: uuid.UUID, actor_id=None,
) -> Person:
    """Merge duplicate INTO primary. Identities, leads, conversations,
    opportunities, consents, profile all move; duplicate is tombstoned."""
    if primary_id == duplicate_id:
        raise ValidationFailed("Cannot merge a person into itself")
    primary = (
        await session.execute(
            select(Person).where(
                Person.id == primary_id, Person.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    duplicate = (
        await session.execute(
            select(Person).where(
                Person.id == duplicate_id, Person.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if primary is None or duplicate is None:
        raise NotFound("Person not found")
    if duplicate.status == "merged":
        raise Conflict("Person already merged")

    # fill blanks on primary from duplicate (never overwrite known data)
    for field in ("phone", "email", "full_name", "company_name"):
        if getattr(primary, field) in (None, "") and getattr(duplicate, field) not in (None, ""):
            setattr(primary, field, getattr(duplicate, field))

    await session.execute(
        update(Identity)
        .where(Identity.tenant_id == tenant_id, Identity.person_id == duplicate_id)
        .values(person_id=primary_id)
    )
    await session.execute(
        update(CommunicationConsent)
        .where(CommunicationConsent.tenant_id == tenant_id,
               CommunicationConsent.person_id == duplicate_id)
        .values(person_id=primary_id)
    )
    await session.execute(
        update(CustomerProfile)
        .where(CustomerProfile.tenant_id == tenant_id,
               CustomerProfile.person_id == duplicate_id)
        .values(person_id=primary_id)
    )
    from app.conversations.models import Conversation

    await session.execute(
        update(Conversation)
        .where(Conversation.tenant_id == tenant_id, Conversation.person_id == duplicate_id)
        .values(person_id=primary_id)
    )
    from app.leads.models import Lead

    await session.execute(
        update(Lead).where(Lead.tenant_id == tenant_id, Lead.person_id == duplicate_id)
        .values(person_id=primary_id)
    )
    await session.execute(
        update(Opportunity)
        .where(Opportunity.tenant_id == tenant_id, Opportunity.person_id == duplicate_id)
        .values(person_id=primary_id)
    )

    duplicate.status = "merged"
    duplicate.merged_into_id = primary_id
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="person.merged", entity_type="person", entity_id=primary_id,
        before={"duplicate_id": str(duplicate_id)},
        after={"merged_into": str(primary_id)},
    )
    await emit(
        session, event_name="person.merged", tenant_id=tenant_id,
        aggregate_type="person", aggregate_id=primary_id,
        payload={"primary_id": str(primary_id), "duplicate_id": str(duplicate_id)},
    )
    return primary
