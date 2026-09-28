"""Invariant: idempotent tenant provisioning (review: onboarding flow)."""

from __future__ import annotations

from sqlalchemy import func, select

from app.organizations.models import Branch, FeatureFlag, Membership, Organization, Role, Tenant
from app.organizations.provisioning import provision_tenant


async def test_provision_creates_full_structure(db):
    async with db.begin():
        tenant = await provision_tenant(
            db, tenant_name="Alpha Estates", org_name="Alpha",
            owner_email="boss@alpha.test", owner_full_name="Boss",
            slug="alpha",
        )
    assert tenant.slug == "alpha"
    org_count = (await db.execute(
        select(func.count()).select_from(Organization).where(Organization.tenant_id == tenant.id)
    )).scalar_one()
    branch_count = (await db.execute(
        select(func.count()).select_from(Branch).where(Branch.tenant_id == tenant.id)
    )).scalar_one()
    role_count = (await db.execute(
        select(func.count()).select_from(Role).where(Role.tenant_id == tenant.id)
    )).scalar_one()
    flag_count = (await db.execute(
        select(func.count()).select_from(FeatureFlag).where(FeatureFlag.tenant_id == tenant.id)
    )).scalar_one()
    membership = (await db.execute(select(Membership).where(Membership.tenant_id == tenant.id))).scalar_one()
    assert org_count == 1
    assert branch_count == 1
    assert role_count == len(__import__("app.core.permissions", fromlist=["SYSTEM_ROLES"]).SYSTEM_ROLES)
    assert flag_count == 6
    assert membership is not None


async def test_provision_is_idempotent(db):
    kwargs = dict(
        tenant_name="Alpha Estates", org_name="Alpha",
        owner_email="boss@alpha.test", owner_full_name="Boss", slug="alpha",
    )
    async with db.begin():
        t1 = await provision_tenant(db, **kwargs)
    async with db.begin():
        t2 = await provision_tenant(db, **kwargs)
    assert t1.id == t2.id
    assert (await db.execute(select(func.count()).select_from(Tenant))).scalar_one() == 1
    assert (await db.execute(select(func.count()).select_from(Organization))).scalar_one() == 1
    assert (await db.execute(select(func.count()).select_from(Branch))).scalar_one() == 1
    assert (await db.execute(select(func.count()).select_from(Role))).scalar_one() == len(
        __import__("app.core.permissions", fromlist=["SYSTEM_ROLES"]).SYSTEM_ROLES
    )
