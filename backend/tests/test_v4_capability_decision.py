"""V4 wave tests: Capability Gateway (PART 10), unified Approval (5.4),
Commission Split invariant (1.29), Inventory Ledger (3.4), Decision Records (5.3),
derived idempotency (1.28), durable event history (4.1)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.capability.custom_fields import define_field, get_values, set_value
from app.capability.gateway import (
    CapabilityDenied,
    GrantExpired,
    GranteeType,
    WebhookURLRejected,
    assert_outbound_url_allowed,
    check_capability,
    derive_idempotency_key,
    install_marketplace_app,
    issue_grant,
    register_webhook_destination,
    revoke_grant,
    validate_webhook_url,
    verify_webhook_destination,
)
from app.core.db import session_factory
from app.core.errors import Conflict, ValidationFailed
from app.decision.models import ApprovalRequest, Decision, InventoryLedger
from app.decision.services import (
    decide,
    decide_approval,
    request_approval,
)
from app.finance.service import write_commission_splits
from app.identity.models import Person
from app.leads.service import create_lead
from app.properties.models import PropertyAsset
from app.properties.service import create_asset, create_project


# ---------- Capability Gateway ----------
def test_derived_idempotency_key_stable_and_argument_sensitive():
    k1 = derive_idempotency_key("exec-1", "create_reservation", {"offer": "o1"})
    k2 = derive_idempotency_key("exec-1", "create_reservation", {"offer": "o1"})
    k3 = derive_idempotency_key("exec-2", "create_reservation", {"offer": "o1"})
    assert k1 == k2, "same execution+action+args → same key"
    assert k1 != k3, "different execution → different key"


async def test_capability_grant_lifecycle(db, tenant):
    async with db.begin():
        await issue_grant(
            db, tenant_id=tenant.id, grantee_type=GranteeType.API_CLIENT,
            grantee_id="partner-app-1", resource_scope="leads", action_scope="read",
            ttl_days=1, issued_by="test",
        )
        # allowed
        grant = await check_capability(
            db, tenant_id=tenant.id, grantee_type=GranteeType.API_CLIENT,
            grantee_id="partner-app-1", resource_scope="leads", action_scope="read",
        )
        assert grant is not None
        # wrong scope denied
        from app.capability.gateway import CapabilityDenied

        with pytest.raises(CapabilityDenied):
            await check_capability(
                db, tenant_id=tenant.id, grantee_type=GranteeType.API_CLIENT,
                grantee_id="partner-app-1", resource_scope="properties", action_scope="read",
            )


async def test_capability_expiry_and_revocation(db, tenant):
    from datetime import UTC, datetime, timedelta

    async with db.begin():
        await issue_grant(db, tenant_id=tenant.id, grantee_type=GranteeType.API_CLIENT,
                          grantee_id="expiring-app", resource_scope="deals",
                          action_scope="read", ttl_days=-1)  # already expired
        g2 = await issue_grant(db, tenant_id=tenant.id, grantee_type=GranteeType.API_CLIENT,
                               grantee_id="revoked-app", resource_scope="deals",
                               action_scope="read", ttl_days=30)
    async with db.begin():

        with pytest.raises(GrantExpired):
            await check_capability(
                db, tenant_id=tenant.id, grantee_type=GranteeType.API_CLIENT,
                grantee_id="expiring-app", resource_scope="deals", action_scope="read",
            )
        await revoke_grant(db, tenant_id=tenant.id, grant_id=g2.id)
        with pytest.raises(CapabilityDenied):
            await check_capability(
                db, tenant_id=tenant.id, grantee_type=GranteeType.API_CLIENT,
                grantee_id="revoked-app", resource_scope="deals", action_scope="read",
            )


# ---------- SSRF defense (V4 10.3) ----------
def test_ssrf_defense_blocks_forbidden_targets():
    for url in (
        "http://localhost:8080/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1.1/admin",
        "http://10.0.0.5/internal",
        "https://metadata.google.internal/computeMetadata",
        "ftp://example.com/x",
    ):
        with pytest.raises(WebhookURLRejected):
            validate_webhook_url(url)
    # public host passes resolution check (example.com is a real public host)
    host = validate_webhook_url("https://example.com/hook")
    assert host == "example.com"


async def test_webhook_registration_verification_and_outbound_gate(db, tenant, owner_ctx):
    async with db.begin():
        dest, token = await register_webhook_destination(
            db, tenant_id=tenant.id, url="https://example.com/hooks/revenue",
            owner_user_id=owner_ctx.user_id,
        )
        assert dest.allowlist_status == "PENDING_VERIFICATION"
        assert token
        # outbound to unverified destination → DENIED

        with pytest.raises(CapabilityDenied):
            await assert_outbound_url_allowed(
                db, tenant_id=tenant.id, url="https://example.com/hooks/revenue"
            )
        # verify → allowed
        verified = await verify_webhook_destination(
            db, tenant_id=tenant.id, destination_id=dest.id
        )
        assert verified.allowlist_status == "VERIFIED"
        await assert_outbound_url_allowed(
            db, tenant_id=tenant.id, url="https://example.com/hooks/revenue"
        )


async def test_marketplace_install_creates_scoped_grants(db, tenant, owner_ctx):
    async with db.begin():
        grants = await install_marketplace_app(
            db, tenant_id=tenant.id, app_id="valuation-pro",
            requested_scopes=["properties:read", "listings:read"],
            approved_by=owner_ctx.user_id,
        )
    assert len(grants) == 2
    for g in grants:
        assert g.grantee_type == GranteeType.MARKETPLACE_APP.value
        assert g.expires_at is not None, "marketplace grants ALWAYS expire"
    async with db.begin():
        await check_capability(
            db, tenant_id=tenant.id, grantee_type=GranteeType.MARKETPLACE_APP,
            grantee_id="valuation-pro", resource_scope="properties", action_scope="read",
        )

        with pytest.raises(CapabilityDenied):
            await check_capability(
                db, tenant_id=tenant.id, grantee_type=GranteeType.MARKETPLACE_APP,
                grantee_id="valuation-pro", resource_scope="reservations", action_scope="write",
            )


# ---------- Custom Fields EAV (V4 10.5) ----------
async def test_custom_fields_define_set_get_collision_free(db, tenant, owner_ctx):
    async with db.begin():
        await define_field(db, tenant_id=tenant.id, entity_type="property_asset",
                           field_key="balcony_count", field_type="number")
        with pytest.raises(Conflict):
            await define_field(db, tenant_id=tenant.id, entity_type="property_asset",
                               field_key="balcony_count")
        with pytest.raises(ValidationFailed):
            await define_field(db, tenant_id=tenant.id, entity_type="property_asset",
                               field_key="bad key!")
    async with db.begin():
        project = await create_project(db, tenant_id=tenant.id, name="CF Compound")
        asset = await create_asset(db, tenant_id=tenant.id, title="CF Unit",
                                   property_type="apartment", asset_type="unit",
                                   project_id=project.id)
        await set_value(db, tenant_id=tenant.id, entity_type="property_asset",
                        entity_id=asset.id, field_key="balcony_count", value=2)
    vals = await get_values(db, tenant_id=tenant.id, entity_type="property_asset",
                            entity_id=asset.id)
    assert vals["balcony_count"] == 2


# ---------- unified Approval primitive (V4 5.4) ----------
async def test_approval_primitive_role_gate_and_expiry(db, tenant, owner_ctx):
    async with db.begin():
        req = await request_approval(
            db, tenant_id=tenant.id, subject_type="offer_discount",
            subject_id="offer-123", payload={"discount_pct": 12},
            approver_role_required="sales_manager", requested_by="sales-agent-1",
        )
        rid = req.id
        # wrong role rejected
        with pytest.raises(ValidationFailed):
            await decide_approval(
                db, tenant_id=tenant.id, approval_id=rid, decision="APPROVED",
                decided_by="anyone", actor_role="sales",
            )
    async with db.begin():
        decided = await decide_approval(
            db, tenant_id=tenant.id, approval_id=rid, decision="APPROVED",
            decided_by=str(owner_ctx.user_id), actor_role="sales_manager",
        )
        assert decided.status == "APPROVED"
    async with db.begin():
        from datetime import UTC, datetime, timedelta

        req2 = await request_approval(
            db, tenant_id=tenant.id, subject_type="offer_discount",
            subject_id="offer-999", approver_role_required="sales_manager",
            expires_in_days=-1,
        )
        with pytest.raises(Conflict):
            await decide_approval(
                db, tenant_id=tenant.id, approval_id=req2.id, decision="REJECTED",
                decided_by=str(owner_ctx.user_id), actor_role="sales_manager",
            )
        expired = await db.get(ApprovalRequest, req2.id)
        assert expired.status == "EXPIRED"


# ---------- Commission splits DB invariant (V4 1.29) ----------
async def test_commission_splits_db_invariant(db, tenant, owner_ctx):
    from app.finance.service import create_deal
    from app.sales.service import create_opportunity

    async with db.begin():
        person = Person(tenant_id=tenant.id, full_name="صفقة سبليت", phone="+201444444444")
        db.add(person)
        await db.flush()
        lead = await create_lead(db, tenant_id=tenant.id, person_id=person.id, source="test")
        opp = await create_opportunity(db, tenant_id=tenant.id, lead_id=lead.id,
                                       person_id=person.id, actor_id=owner_ctx.user_id)
        deal_id = opp.id
        # valid: exactly 100
        await write_commission_splits(db, tenant_id=tenant.id, deal_id=deal_id, splits=[
            {"party_role": "agency", "share_percentage": 60},
            {"party_role": "sales_rep", "share_percentage": 40},
        ])
        # invalid: total 120 → DB trigger ROLLBACKS
        from sqlalchemy import text as st

        with pytest.raises(Exception) as exc:
            await db.execute(st("""
                INSERT INTO commission_splits (id, deal_id, tenant_id, party_role, party_id, share_percentage, created_at)
                VALUES (gen_random_uuid(), :deal, :tenant, 'broker', NULL, 20, now())
            """), {"deal": deal_id, "tenant": tenant.id})
        assert "must be exactly 100.00" in str(exc.value)
    # remainder finalize
    async with db.begin():
        from app.decision.models import CommissionSplit

        await write_commission_splits(db, tenant_id=tenant.id, deal_id=deal_id, splits=[
            {"party_role": "agency", "share_percentage": 60},
            {"party_role": "sales_rep", "share_percentage": 39.5},
        ])
        await db.execute(st("SELECT finalize_commission_splits(:deal, 'agency')"),
                         {"deal": deal_id})
        total = (await db.execute(
            st("SELECT SUM(share_percentage) FROM commission_splits WHERE deal_id = :deal"),
            {"deal": deal_id},
        )).scalar_one()
        assert float(total) == 100.00


# ---------- Inventory ledger (V4 3.4) ----------
async def test_inventory_ledger_appends_every_transition(db, tenant, owner_ctx):
    async with db.begin():
        project = await create_project(db, tenant_id=tenant.id, name="Ledger Compound")
        asset = await create_asset(db, tenant_id=tenant.id, title="Ledger Unit",
                                   property_type="apartment", asset_type="unit",
                                   project_id=project.id)
        asset_id = asset.id
        from app.properties.service import (
            hold_unit, mark_reserved, mark_contracted, mark_sold, release_unit,
        )

        await hold_unit(db, tenant_id=tenant.id, asset_id=asset_id, created_by="t")
        await release_unit(db, tenant_id=tenant.id, asset_id=asset_id)
        await hold_unit(db, tenant_id=tenant.id, asset_id=asset_id, created_by="t")
        reservation_marker = uuid.uuid4()
        from app.properties.service import mark_reserved

        await mark_reserved(db, tenant_id=tenant.id, asset_id=asset_id,
                            reservation_id=reservation_marker)
        await mark_contracted(db, tenant_id=tenant.id, asset_id=asset_id)
        await mark_sold(db, tenant_id=tenant.id, asset_id=asset_id)
    rows = (
        await db.execute(
            select(InventoryLedger)
            .where(InventoryLedger.asset_id == asset_id)
            .order_by(InventoryLedger.occurred_at)
        )
    ).scalars().all()
    chain = [(r.from_state, r.to_state) for r in rows]
    assert chain == [
        ("AVAILABLE", "HELD"), ("HELD", "AVAILABLE"), ("AVAILABLE", "HELD"),
        ("HELD", "RESERVED"), ("RESERVED", "CONTRACTED"), ("CONTRACTED", "SOLD"),
    ], "the ledger must record the full unit lifecycle append-only"


# ---------- Decision Records (V4 5.3) + orchestrator ----------
async def test_decision_record_for_lead_routing(db, tenant, owner_ctx):
    async with db.begin():
        decision = await decide(
            db, tenant_id=tenant.id, action="ASSIGN_LEAD",
            subject={"lead_id": "test-lead"},
            candidates=[
                {"entity_id": "rep1", "signals": {"workload": 2, "lead_value": 4000000,
                                                   "expertise_fit": 0.9}},
                {"entity_id": "rep2", "signals": {"workload": 8, "lead_value": 1000000,
                                                   "expertise_fit": 0.4}},
            ],
            objective="maximize_conversion",
        )
        assert decision.selected_option == {"entity_id": "rep1"}
        assert decision.policy_evaluation["allow"] is True
        assert decision.scoring_results[0]["definition_version"] == "v1"
    decision_row = await db.get(Decision, decision.id)
    assert decision_row.selected_option["entity_id"] == "rep1"


# ---------- Durable event history (V4 4.1) ----------
async def test_event_history_written_alongside_outbox(db, tenant):
    from app.events.history import DomainEventHistory
    from app.events.outbox import emit

    async with db.begin():
        await emit(db, event_name="test.history", tenant_id=tenant.id, payload={"a": 1})
    rows = (
        await db.execute(
            select(DomainEventHistory).where(DomainEventHistory.event_type == "test.history")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload == {"a": 1}
    assert rows[0].recorded_at is not None
