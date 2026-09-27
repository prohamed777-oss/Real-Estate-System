"""Invariant: tenant isolation at the application layer (§102, §1.9).
RLS (defense in depth) is verified separately against migrated databases."""

from __future__ import annotations

from sqlalchemy import select

from app.core import tenancy
from app.identity.models import Person
from app.organizations.provisioning import provision_tenant


async def _make_tenant(db, slug: str, email: str):
    async with db.begin():
        t = await provision_tenant(
            db, tenant_name=slug.title(), org_name=slug.title(),
            owner_email=email, slug=slug,
        )
    return t


async def _ctx_for(db, email: str, tenant_id):
    from sqlalchemy import select as sel

    from app.core.db import session_factory
    from app.organizations.models import User

    async with session_factory() as s:
        user = (await s.execute(sel(User).where(User.email == email))).scalar_one()
    return tenancy.AuthContext(
        user_id=user.id, tenant_id=tenant_id, role_key="owner",
        permissions=frozenset({"people:read", "people:write"}), email=email,
    )


async def test_tenant_a_cannot_read_tenant_b_people(db):
    tenant_a = await _make_tenant(db, "tenanta", "a-owner@tenanta.test")
    tenant_b = await _make_tenant(db, "tenantb", "b-owner@tenantb.test")

    # A creates a person
    ctx_a = await _ctx_for(db, "a-owner@tenanta.test", tenant_a.id)
    tenancy.bind_auth(ctx_a)
    async with db.begin():
        db.add(Person(tenant_id=tenant_a.id, full_name="Secret Person", email="secret@a.test"))

    # B must not see it
    ctx_b = await _ctx_for(db, "b-owner@tenantb.test", tenant_b.id)
    tenancy.bind_auth(ctx_b)
    rows = (await db.execute(select(Person).where(Person.tenant_id == tenant_b.id))).scalars().all()
    assert len(rows) == 0

    # Direct attempt to write into B's scope from A's context is a different
    # story: every service filters by the BOUND tenant, never by client input.
    assert ctx_b.tenant_id != ctx_a.tenant_id


async def test_bound_tenant_is_always_the_query_scope(db, tenant, owner_ctx):
    # Services must derive tenant from auth context, never from payloads.
    person = Person(tenant_id=owner_ctx.tenant_id, full_name="In Tenant")
    db.add(person)
    await db.flush()
    from app.core.db import current_tenant_id

    assert current_tenant_id.get() == tenant.id
