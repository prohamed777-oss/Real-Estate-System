"""Tests for the completion wave: FTS, documents lifecycle, reconciliation,
attribution, content engine, knowledge retrieval, eval runner, quotas, rate limits."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.ai.gateway import ModelResponse, MockModelProvider, set_model_provider
from app.analytics.billing import PLAN_LIMITS, QuotaExceeded, check_quota, record_usage
from app.core.db import session_factory
from app.core.errors import Conflict, DomainError, ValidationFailed
from app.core.ratelimit import RateLimitExceeded, check_rate_limit
from app.finance.documents_service import (
    add_signature,
    add_version,
    create_document,
    decide_approval,
    request_approval,
    sign,
)
from app.identity.models import Person
from app.leads.service import create_lead
from app.marketing.models_ext import MarketingAsset
from app.marketing.service import (
    capture_attribution,
    generate_content,
    publish_content,
    review_content,
)
from app.properties.models import PropertyAsset
from app.properties.reconciliation import resolve_manually, sync_external_state
from app.properties.service import create_asset, create_project


# ---------- FTS (§24) ----------
async def test_fts_ranks_lexical_matches(db, tenant, owner_ctx):
    from sqlalchemy import column

    async with db.begin():
        await create_asset(
            db, tenant_id=tenant.id, title="شقة فاخرة في الشيخ زايد",
            property_type="apartment", location={"city": "الجيزة", "area": "الشيخ زايد"},
        )
        await create_asset(
            db, tenant_id=tenant.id, title="فيلا بالتجمع الخامس",
            property_type="villa", location={"city": "القاهرة", "area": "التجمع الخامس"},
        )
    async with db.begin():
        sv = column("search_vector")  # generated column (migration-managed)
        tsq = func.websearch_to_tsquery("simple", "التجمع")
        rows = (
            await db.execute(
                select(PropertyAsset).where(
                    PropertyAsset.tenant_id == tenant.id, sv.op("@@")(tsq)
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert "التجمع" in rows[0].title


# ---------- documents lifecycle (§36) ----------
async def test_document_versioning_invalidates_approvals(db, tenant, owner_ctx):
    async with db.begin():
        doc = await create_document(
            db, tenant_id=tenant.id, kind="contract", title="عقد بيع A1",
            created_by=str(owner_ctx.user_id), actor_id=owner_ctx.user_id,
        )
        doc_id = doc.id
        approval = await request_approval(db, tenant_id=tenant.id, document_id=doc_id)
        await decide_approval(
            db, tenant_id=tenant.id, approval_id=approval.id, decision="approved",
            approver_id=owner_ctx.user_id,
        )
    async with db.begin():
        doc = await db.get(__import__("app.finance.models", fromlist=["Document"]).Document, doc_id)
        assert doc.status == "approved"
        assert doc.facts_verified is True  # §85: facts usable post-approval
        # new version → back to draft, needs re-approval
        v2 = await add_version(db, tenant_id=tenant.id, document_id=doc_id,
                               change_note="تعديل السعر", created_by=str(owner_ctx.user_id))
        assert v2.version == 2
        doc2 = await db.get(__import__("app.finance.models", fromlist=["Document"]).Document, doc_id)
        assert doc2.status == "draft"
        assert doc2.facts_verified is False


async def test_signature_flow(db, tenant, owner_ctx):
    async with db.begin():
        doc = await create_document(db, tenant_id=tenant.id, kind="contract", title="عقد")
        person = Person(tenant_id=tenant.id, full_name="المشتري", phone="+201888888888")
        db.add(person)
        await db.flush()
        sig = await add_signature(
            db, tenant_id=tenant.id, document_id=doc.id, signer_name="المشتري",
            role="buyer", signer_person_id=person.id, method="digital",
        )
        sig_id = sig.id
    async with db.begin():
        signed = await sign(db, tenant_id=tenant.id, signature_id=sig_id)
        assert signed.status == "signed"
        assert signed.signed_at is not None
    # double-sign → Conflict (§1.7)
    async with db.begin():
        with pytest.raises(Conflict):
            await sign(db, tenant_id=tenant.id, signature_id=sig_id)


# ---------- reconciliation (§19) ----------
async def test_reconciliation_precedence_and_manual_review(db, tenant, owner_ctx):
    async with db.begin():
        project = await create_project(db, tenant_id=tenant.id, name="Rec Compound")
        asset = await create_asset(
            db, tenant_id=tenant.id, title="Rec Unit", property_type="apartment",
            asset_type="unit", project_id=project.id, source_type="internal",
        )
        asset_id = asset.id
        # internal canonical AVAILABLE vs developer (higher precedence, confident, fresh)
        conflicts = await sync_external_state(
            db, tenant_id=tenant.id, asset_id=asset_id,
            external_availability="SOLD", external_source="developer", confidence=90,
            last_synced_at=datetime.now(UTC),
        )
        assert len(conflicts) == 1
        # §19: developer outranks internal + fresh + high confidence → auto-resolved
        assert conflicts[0].status == "auto_resolved"

    async with db.begin():
        # ambiguous low-confidence external claim on price → manual review
        conflicts2 = await sync_external_state(
            db, tenant_id=tenant.id, asset_id=asset_id,
            external_price="9999999", external_source="developer", confidence=60,
            last_synced_at=datetime.now(UTC),
        )
        assert conflicts2[0].status == "manual_review"

    # manual resolution recorded with who/when/what (§19)
    async with db.begin():
        conflict = conflicts[0]
        resolved = await resolve_manually(
            db, tenant_id=tenant.id, conflict_id=conflict.id,
            resolution={"keep": "canonical", "reason": "developer feed stale"},
            resolved_by=str(owner_ctx.user_id),
        )
        assert resolved.status == "resolved"
        assert resolved.resolved_at is not None


# ---------- attribution (§41) ----------
async def test_attribution_captured_on_lead_creation(db, tenant, owner_ctx):
    async with db.begin():
        person = Person(tenant_id=tenant.id, full_name="عميل حملة", phone="+201777777777")
        db.add(person)
        await db.flush()
        lead = await create_lead(
            db, tenant_id=tenant.id, person_id=person.id, source="meta_ads",
            created_by="webhook",
        )
        lead_id = lead.id
    # handler runs via outbox dispatch (async in production)
    async with session_factory() as s:
        async with s.begin():
            from app.events.outbox import dispatch_batch

            await dispatch_batch(s)
    from app.marketing.models import Attribution

    async with db.begin():
        row = (
            await db.execute(select(Attribution).where(Attribution.lead_id == lead_id))
        ).scalar_one_or_none()
        assert row is not None, "attribution must be captured automatically (§41)"
        assert row.source == "meta_ads"
        first_touch = row.first_touch
        await capture_attribution(
            db, tenant_id=tenant.id, lead_id=lead_id, source="direct",
            landing_page="/landing/new",
        )
    row2 = (
        await db.execute(select(Attribution).where(Attribution.lead_id == lead_id))
    ).scalar_one()
    assert row2.first_touch == first_touch
    assert row2.last_touch["source"] == "direct"


# ---------- content engine (§42) ----------
async def test_content_engine_generate_review_publish_gate(db, tenant, owner_ctx):
    mock = MockModelProvider()
    mock.script_response(ModelResponse(text="شقة أحلامك في التجمع الخامس بمساحات فاخرة"))
    set_model_provider(mock)
    try:
        async with db.begin():
            asset = await generate_content(
                db, tenant_id=tenant.id, kind="listing_description",
                brief="شقة 3 غرف التجمع الخامس 130م", actor_id=owner_ctx.user_id,
            )
            assert asset.status == "draft"  # born draft — never publishable directly (§42)
            asset_id = asset.id
            # publish before approval → blocked
            with pytest.raises(Conflict):
                await publish_content(db, tenant_id=tenant.id, asset_id=asset_id)
            await review_content(
                db, tenant_id=tenant.id, asset_id=asset_id, reviewer_id=owner_ctx.user_id,
                decision="approved",
            )
            published = await publish_content(
                db, tenant_id=tenant.id, asset_id=asset_id, actor_id=owner_ctx.user_id,
            )
            assert published.status == "published"
    finally:
        set_model_provider(None)


# ---------- knowledge retrieval (§57) ----------
async def test_knowledge_retrieval_feeds_agent_context(db, tenant, owner_ctx):
    async with db.begin():
        from app.ai.models import KnowledgeDoc

        doc = KnowledgeDoc(
            tenant_id=tenant.id, title="سياسة الصيانة", kind="policy",
            text_content="رسوم الصيانة في كمبوند النخبة 8% من قيمة الوحدة وتُدفع عند التسليم.",
            status="pending",
        )
        db.add(doc)
        await db.flush()
        from app.ai.handlers import embed_knowledge_job

        await embed_knowledge_job(db, tenant.id, {})
        assert doc.status == "ready"

    from app.ai.runtime import retrieve_knowledge

    async with db.begin():
        chunks = await retrieve_knowledge(
            db, tenant_id=tenant.id, query_text="كم رسوم الصيانة في النخبة؟"
        )
    assert len(chunks) >= 1
    assert "الصيانة" in chunks[0]["text"]


# ---------- eval runner (§63) ----------
async def test_eval_runner_scores_tool_selection_and_guardrails(db, tenant, owner_ctx):
    async with db.begin():
        project = await create_project(db, tenant_id=tenant.id, name="Eval Compound",
                                       location={"city": "القاهرة", "area": "التجمع"})
        asset = await create_asset(
            db, tenant_id=tenant.id, title="Eval Unit", property_type="apartment",
            asset_type="unit", project_id=project.id, bedrooms=3,
            location={"city": "القاهرة", "area": "التجمع"},
        )
        from app.matching.service import build_search_document

        await build_search_document(db, tenant_id=tenant.id, asset_id=asset.id)

    mock = MockModelProvider()
    mock.script_response(
        ModelResponse(tool_calls=[{"name": "search_properties", "args": {"area": "التجمع"}}]),
        ModelResponse(text="لقيت شقة مناسبة في التجمع الخامس متاحة للمعاينة."),
    )
    set_model_provider(mock)
    try:
        from app.ai.evaluation import run_evaluation

        async with db.begin():
            run = await run_evaluation(
                db, tenant_id=tenant.id, agent_key="matching",
                cases=[{
                    "input": "عايز شقة في التجمع",
                    "expected_tools": ["search_properties"],
                    "must_contain": ["التجمع"],
                    "must_not_contain": ["أضمن"],
                }],
                dataset_name="unit-test",
            )
        assert run.metrics["passed"] == 1
        assert run.metrics["tool_selection_accuracy"] == 1.0
    finally:
        set_model_provider(None)


# ---------- quotas (§99) ----------
async def test_quota_enforcement_blocks_when_exhausted(db, tenant, owner_ctx):
    async with db.begin():
        await record_usage(db, tenant_id=tenant.id, kind="ai_requests", quantity=500)
        # trial plan: 500 ai_requests/month → exhausted
        with pytest.raises(QuotaExceeded):
            await check_quota(db, tenant_id=tenant.id, kind="ai_requests")
        # messages still fine
        result = await check_quota(db, tenant_id=tenant.id, kind="messages")
        assert result["allowed"] is True
        # upgrade plan → quota opens
        from app.analytics.billing import get_subscription

        sub = await get_subscription(db, tenant.id)
        sub.plan = "pro"
        result2 = await check_quota(db, tenant_id=tenant.id, kind="ai_requests")
        assert result2["allowed"] is True


def test_plan_limits_exist():
    for plan in ("trial", "starter", "pro", "enterprise"):
        assert plan in PLAN_LIMITS


# ---------- rate limiting (§98) ----------
async def test_rate_limit_fixed_window(db, owner_ctx):
    key = f"tenant:{uuid.uuid4()}:test"
    async with db.begin():
        for _ in range(3):
            result = await check_rate_limit(db, key=key, limit=3, window_seconds=60)
    assert result["count"] == 3
    async with db.begin():
        with pytest.raises(RateLimitExceeded):
            await check_rate_limit(db, key=key, limit=3, window_seconds=60)
    # different key unaffected
    async with db.begin():
        ok = await check_rate_limit(db, key=key + "-other", limit=3, window_seconds=60)
    assert ok["allowed"] is True


# ---------- AI quota gate wired into execute endpoint ----------
async def test_ai_execute_blocked_by_quota(db, tenant, owner_ctx, api):
    async with db.begin():
        for _ in range(500):
            pass  # filler to keep the transaction meaningful
        from app.analytics.billing import record_usage

        await record_usage(db, tenant_id=tenant.id, kind="ai_requests", quantity=500)
    res = await api.post(
        "/api/v1/ai/agents/reception/execute",
        json={"message": "مرحبا"},
        headers={"X-Dev-Email": "owner@acme.test"},
    )
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "quota_exceeded"
