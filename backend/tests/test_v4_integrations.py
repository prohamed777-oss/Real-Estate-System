"""V4-3: integration tests — Claims, Decision routing, email, commission remainder."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.claims.service import Claim, assert_claim
from app.core.db import session_factory
from app.decision.models import CommissionSplit, Decision
from app.identity.models import Person
from app.leads.models import Lead
from app.leads.service import create_lead
from app.properties.service import create_asset, create_project, set_price


# ---------- helpers ----------
def _person(db, tenant, name, phone):
    p = Person(tenant_id=tenant.id, full_name=name, phone=phone)
    db.add(p)
    db.flush()
    return p


async def _sales_rep(db, tenant, org):
    from app.organizations.models import Membership, Role, User
    role = (await db.execute(
        select(Role).where(Role.tenant_id == tenant.id, Role.key == "sales")
    )).scalar_one()
    user = User(tenant_id=tenant.id, email=f"rep-{uuid.uuid4().hex[:6]}@test.io", full_name="Rep")
    db.add(user)
    await db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id,
                       organization_id=org.id, role_id=role.id))
    await db.flush()
    return user.id


async def _get_org(db, tenant):
    from app.organizations.models import Organization
    return (await db.execute(
        select(Organization).where(Organization.tenant_id == tenant.id)
    )).scalar_one()


async def _dispatch():
    async with session_factory() as s:
        async with s.begin():
            from app.events.outbox import dispatch_batch

            await dispatch_batch(s)


# ---------- claims on price/availability ----------
async def test_price_change_creates_verified_claim(db, tenant, owner_ctx):
    async with db.begin():
        project = await create_project(db, tenant_id=tenant.id, name="Claim C")
        asset = await create_asset(db, tenant_id=tenant.id, title="Claim U",
                                   property_type="apartment", asset_type="unit",
                                   project_id=project.id)
        await set_price(db, tenant_id=tenant.id, asset_id=asset.id, amount="5000000")
    await _dispatch()
    async with session_factory() as s:
        claims = (
            await s.execute(
                select(Claim).where(
                    Claim.tenant_id == tenant.id, Claim.field == "price"
                )
            )
        ).scalars().all()
    assert len(claims) >= 1
    assert claims[0].truth_status == "CURRENT"
    assert claims[0].confidence >= 95


async def test_availability_claims_on_reserve(db, tenant, owner_ctx):
    async with db.begin():
        project = await create_project(db, tenant_id=tenant.id, name="Avail C")
        asset = await create_asset(db, tenant_id=tenant.id, title="Avail U",
                                   property_type="apartment", asset_type="unit",
                                   project_id=project.id)
        from app.properties.service import mark_reserved
        await mark_reserved(db, tenant_id=tenant.id, asset_id=asset.id,
                            reservation_id=uuid.uuid4())
    await _dispatch()
    async with session_factory() as s:
        claims = (
            await s.execute(
                select(Claim).where(
                    Claim.tenant_id == tenant.id,
                    Claim.entity_id == asset.id, Claim.field == "availability"
                )
            )
        ).scalars().all()
    assert len(claims) >= 1
    assert any(c.value["v"]["state"] == "RESERVED" for c in claims)


# ---------- decision routing ----------
async def test_lead_routing_decision_record(db, tenant, owner_ctx):
    """V4 5.5: lead creation triggers Decision Plane routing."""
    from app.decision.models import Decision as DecisionRow
    from app.finance.service import write_commission_splits  # noqa: F401
    from app.organizations.models import Membership, Organization, Role, User
    from app.leads.service import create_lead
    from app.identity.models import Person

    async with db.begin():
        # setup: org, sales rep, person, lead
        org = (await db.execute(
            select(Organization).where(Organization.tenant_id == tenant.id)
        )).scalar_one()
        role = (await db.execute(
            select(Role).where(Role.tenant_id == tenant.id, Role.key == "sales")
        )).scalar_one()
        rep_user = User(tenant_id=tenant.id, email=f"rep-{uuid.uuid4().hex[:6]}@test.io",
                         full_name="Sales Rep")
        db.add(rep_user)
        await db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=rep_user.id,
                           organization_id=org.id, role_id=role.id))
        person = Person(tenant_id=tenant.id, full_name="Route P", phone="+201555999999")
        db.add(person)
        await db.flush()
        lead = await create_lead(db, tenant_id=tenant.id, person_id=person.id, source="test")
        lead_id = lead.id

    # dispatch outbox — triggers decision handler
    await _dispatch()

    async with session_factory() as s:
        decisions = (
            await s.execute(
                select(DecisionRow).where(
                    DecisionRow.tenant_id == tenant.id, DecisionRow.action == "ASSIGN_LEAD"
                )
            )
        ).scalars().all()
    assert len(decisions) >= 1, "lead routing must produce a Decision Record"
    assert decisions[0].policy_evaluation["allow"] is True



# ---------- email delivery ----------
async def test_email_delivery_pipeline(db, tenant, owner_ctx):
    from app.automation.email_delivery import deliver_queued_notifications
    from app.automation.models import Notification
    from app.organizations.models import User

    async with db.begin():
        user = (await db.execute(
            select(User).where(User.email == "owner@acme.test")
        )).scalar_one()
        db.add(Notification(
            tenant_id=tenant.id, kind="email", recipient_type="user",
            recipient_id=str(user.id), title="Test Email", body="Hello",
            status="queued",
        ))
    async with db.begin():
        sent = await deliver_queued_notifications(db, limit=10)
    assert sent == 0  # no RESEND_API_KEY → gracefully skipped


# ---------- commission remainder ----------
async def test_commission_splits_remainder_function(db, tenant, owner_ctx):
    deal_id = uuid.uuid4()
    async with db.begin():
        await write_commission_splits(db, tenant_id=tenant.id, deal_id=deal_id, splits=[
            {"party_role": "agency", "share_percentage": 60.0},
            {"party_role": "sales_rep", "share_percentage": 30.0},
        ])
        await db.execute(text("SELECT finalize_commission_splits(:deal, 'agency')"),
                         {"deal": str(deal_id)})
        total = (await db.execute(
            text("SELECT SUM(share_percentage) FROM commission_splits WHERE deal_id = :deal"),
            {"deal": str(deal_id)},
        )).scalar_one()
    assert float(total) == 100.00


from app.finance.service import write_commission_splits  # noqa: E402
from unittest.mock import patch as mock_patch  # noqa: E402
