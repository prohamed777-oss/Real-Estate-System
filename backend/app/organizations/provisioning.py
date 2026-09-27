"""Tenant provisioning (§114 Phase 1) — IDEMPOTENT bootstrap.

    Signup → Tenant → Organization → Owner → default roles → default settings
    → default feature flags → channel configuration shell → provision complete

Re-running with the same slug/owner email completes the structure without
duplicating anything (review rule: idempotent tenant provisioning).
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, ValidationFailed
from app.core.permissions import SYSTEM_ROLES
from app.events.outbox import emit
from app.identity.models import Person
from app.organizations.models import (
    Branch,
    FeatureFlag,
    Membership,
    Organization,
    Role,
    RolePermission,
    Team,
    Tenant,
    User,
)

DEFAULT_FLAGS = {
    "voice_agent": False,
    "smart_matching_v2": False,
    "ai_reactivation": False,
    "market_intelligence": False,
    "broker_network": False,
    "advanced_deals": False,
}


def slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9\u0600-\u06FF]+", "-", value.strip().lower()).strip("-")
    return s or "tenant"


async def seed_roles(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Create the tenant's system roles + permissions. Idempotent."""
    roles = (
        await session.execute(select(Role).where(Role.tenant_id == tenant_id, Role.is_system.is_(True)))
    ).scalars().all()
    by_key = {r.key: r for r in roles}
    for key, permissions in SYSTEM_ROLES.items():
        role = by_key.get(key)
        if role is None:
            role = Role(tenant_id=tenant_id, key=key, name=key.replace("_", " ").title(), is_system=True)
            session.add(role)
            await session.flush()
            by_key[key] = role
        existing_perms = set(
            (
                await session.execute(
                    select(RolePermission.permission).where(RolePermission.role_id == role.id)
                )
            ).scalars()
        )
        for perm in permissions:
            if perm not in existing_perms:
                session.add(RolePermission(role_id=role.id, permission=perm))
    await session.flush()
    return {k: r.id for k, r in by_key.items()}


async def get_or_create_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    full_name: str = "",
    user_id: uuid.UUID | None = None,
    phone: str | None = None,
    locale: str = "ar",
) -> User:
    """Fetch or create the internal user row. user_id comes from Supabase auth."""
    user = (await session.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
    if user is None:
        user = User(
            id=user_id or uuid.uuid4(),
            tenant_id=tenant_id,
            email=email.lower(),
            full_name=full_name,
            phone=phone,
            locale=locale,
        )
        session.add(user)
        await session.flush()
    elif user.tenant_id != tenant_id:
        raise Conflict(f"User {email} belongs to a different tenant")
    return user


async def get_or_create_person(
    session: AsyncSession, *, tenant_id: uuid.UUID, full_name: str = "", email: str | None = None,
    phone: str | None = None, person_type: str = "customer", locale: str = "ar",
) -> Person:
    if email:
        existing = (
            await session.execute(
                select(Person).where(
                    Person.tenant_id == tenant_id,
                    Person.email == email.lower(),
                    Person.status == "active",
                )
            )
        ).scalar_one_or_none()
        if existing:
            return existing
    person = Person(
        tenant_id=tenant_id,
        type=person_type,
        full_name=full_name,
        email=email.lower() if email else None,
        phone=phone,
        locale=locale,
    )
    session.add(person)
    await session.flush()
    return person


async def provision_tenant(
    session: AsyncSession,
    *,
    tenant_name: str,
    org_name: str,
    owner_email: str,
    owner_full_name: str = "",
    owner_user_id: uuid.UUID | None = None,
    slug: str | None = None,
    locale: str = "ar",
    currency: str = "EGP",
    timezone: str = "Africa/Cairo",
) -> Tenant:
    """Full idempotent provisioning. Returns the tenant."""
    if not tenant_name or not owner_email:
        raise ValidationFailed("tenant_name and owner_email are required")
    slug_v = slugify(slug or tenant_name)

    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug_v))).scalar_one_or_none()
    created = tenant is None
    if tenant is None:
        tenant = Tenant(
            name=tenant_name,
            slug=slug_v,
            locale=locale,
            currency=currency,
            timezone=timezone,
            settings={"provisioned_via": "bootstrap"},
        )
        session.add(tenant)
        await session.flush()
        await audit(
            session,
            tenant_id=tenant.id,
            actor_type="system",
            actor_id=None,
            action="tenant.created",
            entity_type="tenant",
            entity_id=tenant.id,
            after={"slug": tenant.slug, "name": tenant.name},
            source="seed",
        )

    org = (
        await session.execute(select(Organization).where(Organization.tenant_id == tenant.id))
    ).scalar_one_or_none()
    if org is None:
        org = Organization(
            tenant_id=tenant.id, name=org_name or tenant_name, locale=locale, currency=currency, timezone=timezone
        )
        session.add(org)
        await session.flush()

    role_ids = await seed_roles(session, tenant.id)

    branch = (
        await session.execute(select(Branch).where(Branch.tenant_id == tenant.id).limit(1))
    ).scalar_one_or_none()
    if branch is None:
        branch = Branch(tenant_id=tenant.id, organization_id=org.id, name="Main Branch", code="MAIN",
                        timezone=timezone)
        session.add(branch)
        await session.flush()
        session.add(
            Team(tenant_id=tenant.id, organization_id=org.id, branch_id=branch.id, name="Sales",
                 department="sales")
        )

    if created:
        # NEW tenant: its owner is provisioned normally
        owner = await get_or_create_user(
            session,
            tenant_id=tenant.id,
            email=owner_email,
            full_name=owner_full_name,
            user_id=owner_user_id,
            locale=locale,
        )
        session.add(
            Membership(
                tenant_id=tenant.id,
                user_id=owner.id,
                organization_id=org.id,
                branch_id=branch.id,
                role_id=role_ids["owner"],
                is_primary=True,
            )
        )
    else:
        # EXISTING tenant: bootstrap must NEVER attach a new owner (V4 audit #4).
        # Structure top-up only happens for someone who is ALREADY a member.
        existing_owner_membership = (
            await session.execute(
                select(Membership).where(
                    Membership.tenant_id == tenant.id,
                    Membership.role_id == role_ids["owner"],
                )
            )
        ).scalars().first()
        if existing_owner_membership is not None:
            existing_owner_user = await session.get(User, existing_owner_membership.user_id)
            if existing_owner_user and existing_owner_user.email != owner_email.lower():
                pass  # different email → no membership is created, nothing is linked
    await session.flush()

    existing_flags = set(
        (
            await session.execute(select(FeatureFlag.key).where(FeatureFlag.tenant_id == tenant.id))
        ).scalars()
    )
    for flag_key, enabled in DEFAULT_FLAGS.items():
        if flag_key not in existing_flags:
            session.add(FeatureFlag(tenant_id=tenant.id, key=flag_key, enabled=enabled))

    if created:
        await emit(
            session,
            event_name="tenant.provisioned",
            tenant_id=tenant.id,
            aggregate_type="tenant",
            aggregate_id=tenant.id,
            payload={"slug": tenant.slug, "owner_email": owner_email},
        )
    await session.flush()
    return tenant
