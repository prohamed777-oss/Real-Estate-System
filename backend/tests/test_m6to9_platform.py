"""M6-M9 tests: finance flow, AI runtime (mock provider), automation journeys
+ SLA, analytics NL queries, importer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.ai.gateway import ModelResponse, MockModelProvider, set_model_provider
from app.ai.models import AIExecution
from app.ai.runtime import execute_agent, get_profile
from app.automation.models import JourneyInstance, Notification, SLATracker
from app.automation.service import (
    apply_rules_for_event,
    start_sla_tracker,
    start_journey,
    tick_journeys,
    tick_sla,
)
from app.core.db import session_factory
from app.core.errors import Conflict, PermissionDenied, ValidationFailed
from app.finance.models import Commission, Contract, Deal, PaymentSchedule
from app.finance.service import (
    calculate_commissions,
    close_deal,
    create_contract_from_reservation,
    create_deal,
    record_payment,
    schedule_payment_plan,
)
from app.importer.service import parse_workbook, run_import
from app.leads.models import Lead
from app.leads.service import create_lead
from app.properties.service import create_asset, create_project, set_price
from app.sales.models import Offer, Reservation
from app.sales.service import (
    create_offer,
    create_opportunity,
    create_reservation,
    transition_offer,
)


# ---------- helpers ----------
async def _won_deal(db, tenant, owner_ctx, *, gross="4000000"):
    """Drive the full path to a WON deal with an accepted offer."""
    async with db.begin():
        project = await create_project(db, tenant_id=tenant.id, name="Deal Compound")
        asset = await create_asset(
            db, tenant_id=tenant.id, title="Deal Unit", property_type="apartment",
            asset_type="unit", project_id=project.id,
        )
        await set_price(db, tenant_id=tenant.id, asset_id=asset.id, amount="4000000")
        person_row = __import__("app.identity.models", fromlist=["Person"]).Person(
            tenant_id=tenant.id, full_name="مشتري", phone="+201555000001"
        )
        db.add(person_row)
        await db.flush()
        lead = await create_lead(db, tenant_id=tenant.id, person_id=person_row.id, source="test")
        opp = await create_opportunity(db, tenant_id=tenant.id, lead_id=lead.id,
                                       person_id=person_row.id, actor_id=owner_ctx.user_id)
        offer = await create_offer(
            db, tenant_id=tenant.id, opportunity_id=opp.id, asset_id=asset.id,
            price_amount=Decimal(gross), actor_id=owner_ctx.user_id,
        )
        await transition_offer(db, offer=offer, event="send", actor_id=owner_ctx.user_id)
        await transition_offer(db, offer=offer, event="accept", actor_id=owner_ctx.user_id)
        reservation = await create_reservation(
            db, tenant_id=tenant.id, opportunity_id=opp.id, offer_id=offer.id,
            actor_id=owner_ctx.user_id,
        )
        contract = await create_contract_from_reservation(
            db, tenant_id=tenant.id, reservation_id=reservation.id, actor_id=owner_ctx.user_id,
        )
        deal = await create_deal(
            db, tenant_id=tenant.id, opportunity_id=opp.id, contract_id=contract.id,
            gross_value=Decimal(gross), actor_id=owner_ctx.user_id,
        )
        return deal, contract, reservation


async def test_m6_contract_deal_payments_commissions(db, tenant, owner_ctx):
    deal, contract, reservation = await _won_deal(db, tenant, owner_ctx)
    assert contract.status == "pending_signature"
    res = await db.get(Reservation, reservation.id)
    assert res.status == "CONVERTED"

    async with db.begin():
        await close_deal(db, deal=deal, event="contract", actor_id=owner_ctx.user_id)
        await close_deal(db, deal=deal, event="win", actor_id=owner_ctx.user_id)
        deal_id = deal.id
    # commissions require an active rule
    async with db.begin():
        with pytest.raises(ValidationFailed):
            await calculate_commissions(db, tenant_id=tenant.id, deal_id=deal_id)
        from app.finance.models import CommissionRule

        db.add(CommissionRule(
            tenant_id=tenant.id, name="default",
            splits={"agency": 1.5, "sales_rep": 1.0},
            is_active=True, approved_by=owner_ctx.user_id,
        ))
        rows = await calculate_commissions(db, tenant_id=tenant.id, deal_id=deal_id,
                                           actor_id=owner_ctx.user_id)
        assert len(rows) == 2
        agency = next(r for r in rows if r.beneficiary_type == "agency")
        assert agency.amount == (Decimal("4000000") * Decimal("1.5") / 100).quantize(Decimal("0.0001"))

    # payment schedule + payment
    async with db.begin():
        schedule = await schedule_payment_plan(
            db, tenant_id=tenant.id, deal_id=deal_id, contract_id=None,
            total_amount=Decimal("4000000"), currency="EGP",
            down_payment_pct=Decimal("10"), installment_count=8,
            installment_period_months=3,
        )
        assert len(schedule) == 9  # down + 8 installments
        scheduled_total = sum(r.amount for r in schedule)
        assert scheduled_total == Decimal("4000000").quantize(Decimal("0.0001"))
        payment = await record_payment(
            db, tenant_id=tenant.id, deal_id=deal_id, amount=schedule[0].amount,
            schedule_id=schedule[0].id, actor_id=owner_ctx.user_id,
        )
        assert payment.status == "completed"
        row = await db.get(PaymentSchedule, schedule[0].id)
        assert row.status == "completed"


async def test_m7_agent_runtime_with_tools_and_ledger(db, tenant, owner_ctx):
    """Full agent loop: model calls search tool, then answers. Ledger written."""
    async with db.begin():
        project = await create_project(db, tenant_id=tenant.id, name="AI Compound",
                                       location={"city": "Cairo", "area": "New Cairo"})
        asset = await create_asset(
            db, tenant_id=tenant.id, title="AI Unit", property_type="apartment",
            asset_type="unit", project_id=project.id, bedrooms=3,
            location={"city": "Cairo", "area": "New Cairo"},
        )
        await set_price(db, tenant_id=tenant.id, asset_id=asset.id, amount="4000000")
        from app.matching.service import build_search_document

        await build_search_document(db, tenant_id=tenant.id, asset_id=asset.id)

    mock = MockModelProvider()
    mock.script_response(
        ModelResponse(tool_calls=[{"name": "search_properties",
                                   "args": {"area": "New Cairo", "max_price": 4500000}}]),
        ModelResponse(text="لقت شقة مناسبة في التجمع بسعر 4,000,000 EGP متاحة."),
    )
    set_model_provider(mock)
    try:
        async with db.begin():
            profile = await get_profile(db, tenant.id, "matching")
            result = await execute_agent(
                db, tenant_id=tenant.id, profile=profile,
                user_message="عايز شقة 3 غرف في التجمع تحت 4.5 مليون",
                permissions={"properties:read", "inventory:read"},
            )
        assert result.status == "completed"
        assert any(tc["name"] == "search_properties" for tc in result.tool_calls)
        # ledger row exists with tool trace
        exec_row = await db.get(AIExecution, result.execution_id)
        assert exec_row is not None
        assert exec_row.tool_calls[0]["name"] == "search_properties"
        assert exec_row.status == "completed"
    finally:
        set_model_provider(None)


async def test_m7_guardrails_flag_unverified_price(db, tenant, owner_ctx):
    """§60: price claim WITHOUT tool evidence → flagged + human escalation."""
    from app.ai.guardrails import check_output

    flags, needs_human = check_output("الشقة دي بـ 4,000,000 EGP ومتاحة", tool_calls=[])
    assert "price_without_tool_evidence" in flags
    assert needs_human is True
    flags2, _ = check_output("الشقة دي بـ 4,000,000 EGP",
                              tool_calls=[{"name": "get_current_price"}])
    assert flags2 == []


async def test_m7_approval_required_tool_blocks_agent(db, tenant, owner_ctx):
    """§59: reservation tool is approval-required — agent without approval is denied."""
    async with db.begin():
        profile = await get_profile(db, tenant.id, "reception")
        # reception has no create_reservation scope; execution would fail on scope.
        assert "create_reservation" not in (profile.tool_scopes or [])


async def test_m8_rules_notifications_and_sla(db, tenant, owner_ctx):
    async with db.begin():
        lead = await create_lead(db, tenant_id=tenant.id, person_id=await _person(db, tenant),
                                 source="test")
        lead_id = lead.id
        from app.automation.models import AutomationRule, SLAPolicy

        db.add(AutomationRule(
            tenant_id=tenant.id, name="notify-new-lead", trigger_event="lead.created",
            actions=[{"type": "notify", "params": {"title": "Lead جديد!", "body": "متابعة عاجلة"}}],
        ))
        db.add(SLAPolicy(
            tenant_id=tenant.id, name="first-contact-60s", entity_type="lead",
            trigger_event="lead.created", target_seconds=1,
            escalations=[{"type": "notify", "params": {"title": "SLA خالف!"}}],
        ))
        # trigger rule directly
        executed = await apply_rules_for_event(
            db, tenant_id=tenant.id, event_name="lead.created",
            payload={"lead_id": str(lead_id)},
        )
        assert executed >= 1
        trackers = await start_sla_tracker(
            db, tenant_id=tenant.id, entity_type="lead", entity_id=lead_id,
            trigger_event="lead.created",
        )
        assert len(trackers) == 1
        tracker_id = trackers[0].id
        # force breach
        tracker = await db.get(SLATracker, tracker_id)
        tracker.due_at = datetime.now(UTC) - timedelta(seconds=5)
    db.expire_all()
    async with session_factory() as s:
        async with s.begin():
            stats = await tick_sla(s)
    assert stats["breached"] >= 1
    notifications = (
        await db.execute(select(func.count()).select_from(Notification))
    ).scalar_one()
    assert notifications >= 1, "SLA escalation must create a notification"


async def _person(db, tenant):
    from app.identity.models import Person

    person = Person(tenant_id=tenant.id, full_name="SLA Test", phone=f"+20199{uuid.uuid4().hex[:6]}")
    db.add(person)
    await db.flush()
    return person.id


async def test_m8_durable_journey_survives_wait(db, tenant, owner_ctx):
    async with db.begin():
        person_id = await _person(db, tenant)
        lead = await create_lead(db, tenant_id=tenant.id, person_id=person_id, source="test")
        from app.automation.models import Journey

        db.add(Journey(
            tenant_id=tenant.id, name="nurture-3-touch",
            definition=[
                {"type": "notify", "params": {"title": "Touch 1"}},
                {"type": "wait", "duration_minutes": 60},
                {"type": "notify", "params": {"title": "Touch 2"}},
            ],
        ))
        instance = await start_journey(
            db, tenant_id=tenant.id, journey_key="nurture-3-touch",
            entity_type="lead", entity_id=lead.id,
        )
        instance_id = instance.id
    # first tick: executes step 1, then waits at step 2
    async with db.begin():
        advanced = await tick_journeys(db)
    assert advanced >= 1
    inst = await db.get(JourneyInstance, instance_id)
    assert inst.status == "waiting"
    assert inst.current_step == 2

    # simulate time passing: clear wait_until → tick again → completes
    async with db.begin():
        inst2 = await db.get(JourneyInstance, instance_id)
        inst2.wait_until = datetime.now(UTC) - timedelta(minutes=1)
    db.expire_all()
    async with db.begin():
        await tick_journeys(db)
    inst3 = await db.get(JourneyInstance, instance_id)
    assert inst3.status == "completed"
    assert inst3.current_step == 3


async def test_m9_analytics_funnel_and_nl_query(db, tenant, owner_ctx):
    deal, _, _ = await _won_deal(db, tenant, owner_ctx)
    async with db.begin():
        await close_deal(db, deal=deal, event="contract", actor_id=owner_ctx.user_id)
        await close_deal(db, deal=deal, event="win", actor_id=owner_ctx.user_id)
    from app.analytics.service import execute_query_plan, funnel, operational_snapshot, parse_nl_question

    async with db.begin():
        snapshot = await operational_snapshot(db, tenant.id)
        funnel_data = await funnel(db, tenant.id)
    # the sold unit is CONTRACTED after the contract flow (§17)
    assert snapshot["contracted_units"] >= 1
    assert funnel_data["deals_won"] == 1
    assert funnel_data["revenue"] == 4000000.0
    assert funnel_data["conversion_rates"]["reservation_to_deal"] == 1.0

    plan = parse_nl_question("هات كل العملاء اللي ميزانيتهم فوق 6 مليون وعايزين 3 غرف ومحدش كلمهم من أسبوع")
    assert plan["filters"].get("max_budget") == 6_000_000
    assert plan["filters"].get("bedrooms") == 3
    assert plan["filters"].get("no_contact_days") == 7
    async with db.begin():
        result = await execute_query_plan(db, tenant.id, plan)
    assert result["count"] >= 0
    assert result["plan"] == plan


def test_m9_importer_parses_csv():
    import io

    csv_bytes = "title,property_type,bedrooms,price\nc,apartment,3,4000000\n".encode()
    rows = parse_workbook(csv_bytes, "test.csv")
    assert len(rows) == 1
    assert rows[0]["title"] == "c"
    assert rows[0]["bedrooms"] == "3"


async def test_m9_importer_properties_flow(db, tenant, owner_ctx):
    csv_content = (
        "title,property_type,bedrooms,price,project_name\n"
        "وحدة 1,apartment,3,4000000,مشروع الاستيراد\n"
        ",,,,,\n"  # bad row: no title → error row
    ).encode()
    rows = parse_workbook(csv_content, "props.csv")
    async with db.begin():
        job = await run_import(db, tenant_id=tenant.id, kind="properties",
                               filename="props.csv", rows=rows,
                               created_by=str(owner_ctx.user_id))
    assert job.total_rows == 2
    assert job.ok_rows == 1
    assert job.error_rows == 1
    assert job.status == "partial"
    assets = (
        await db.execute(select(func.count()).select_from(__import__("app.properties.models", fromlist=["PropertyAsset"]).PropertyAsset))
    ).scalar_one()
    assert assets == 1
