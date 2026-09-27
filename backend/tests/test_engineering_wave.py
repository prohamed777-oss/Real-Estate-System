"""Engineering-judgment wave tests: credentials encryption, DLQ, person merge,
WhatsApp templates, media storage, offer document, retention."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.channels.models import ChannelAccount
from app.channels.service import connect_channel_account
from app.channels.templates import create_template, render_template, send_templated_message
from app.core.crypto import decrypt_config, encrypt_config
from app.core.db import session_factory
from app.core.errors import Conflict, ValidationFailed
from app.events.queue import enqueue
from app.identity.merge import find_duplicates, merge_persons
from app.identity.models import Person


# ---------- credentials encryption ----------
def test_encrypt_decrypt_roundtrip_and_plaintext_passthrough():
    cfg = {"access_token": "EAAG-secret", "phone_number_id": "123", "app_secret": "s3cr3t"}
    stored = encrypt_config(cfg)
    assert "_encrypted" in stored, "sensitive keys must be encrypted at rest"
    assert "EAAG-secret" not in str(stored)
    assert "123" in str(stored), "non-sensitive fields stay readable"
    assert decrypt_config(stored) == cfg
    # config without secrets passes through untouched
    plain = {"phone_number_id": "123"}
    assert encrypt_config(plain) == plain


async def test_channel_account_stores_encrypted_config(db, tenant, owner_ctx):
    async with db.begin():
        await connect_channel_account(
            db, tenant_id=tenant.id, channel="whatsapp", provider="meta_whatsapp",
            config={"access_token": "TOPSECRET", "phone_number_id": "999"},
            actor_id=owner_ctx.user_id,
        )
        row = (
            await db.execute(select(ChannelAccount).where(ChannelAccount.provider == "meta_whatsapp"))
        ).scalar_one()
    assert "TOPSECRET" not in str(row.config), "token must never sit in plaintext"
    assert "_encrypted" in row.config


# ---------- DLQ ----------
async def test_dlq_view_and_requeue(db, tenant, owner_ctx, api):
    async with db.begin():
        await enqueue(db, job_type="no.such.handler", tenant_id=tenant.id, payload={},
                      max_attempts=1)
    from app.events.queue import run_batch

    async with session_factory() as s:
        async with s.begin():
            await run_batch(s)
    secret = {"X-Cron-Secret": "test-cron-secret"}
    res = await api.get("/api/v1/ops/dlq", headers=secret)
    assert res.status_code == 200
    dead = res.json()["jobs"]
    assert any(j["type"] == "no.such.handler" for j in dead)
    dead_id = next(j["id"] for j in dead if j["type"] == "no.such.handler")
    res2 = await api.post(f"/api/v1/ops/dlq/jobs/{dead_id}/requeue", headers=secret)
    assert res2.status_code == 200
    assert res2.json()["status"] == "pending"
    # guarded
    res3 = await api.get("/api/v1/ops/dlq")
    assert res3.status_code == 401


# ---------- person merge ----------
async def test_duplicate_detection_and_merge(db, tenant, owner_ctx):
    async with db.begin():
        p1 = Person(tenant_id=tenant.id, full_name="محمد أحمد", phone="+201001234567")
        p2 = Person(tenant_id=tenant.id, full_name="محمد احمد", phone="01001234567")
        db.add_all([p1, p2])
        await db.flush()
        p1_id, p2_id = p1.id, p2.id

    async with db.begin():
        dupes = await find_duplicates(db, tenant_id=tenant.id)
    assert any(set(d["person_ids"]) == {str(p1_id), str(p2_id)} for d in dupes)

    async with db.begin():
        primary = await merge_persons(
            db, tenant_id=tenant.id, primary_id=p1_id, duplicate_id=p2_id,
            actor_id=owner_ctx.user_id,
        )
    assert primary.phone == "+201001234567"
    dup = await db.get(Person, p2_id)
    assert dup.status == "merged"
    assert dup.merged_into_id == p1_id
    # double merge blocked
    async with db.begin():
        with pytest.raises(Conflict):
            await merge_persons(db, tenant_id=tenant.id, primary_id=p1_id,
                                duplicate_id=p2_id)
    # self merge blocked
    async with db.begin():
        with pytest.raises(ValidationFailed):
            await merge_persons(db, tenant_id=tenant.id, primary_id=p1_id,
                                duplicate_id=p1_id)


# ---------- WhatsApp templates ----------
def test_template_rendering_missing_vars():
    body = "مرحبًا {{name}}، وحدة {{unit}} متاحة"
    assert render_template(body, {"name": "أحمد", "unit": "A1"}) == "مرحبًا أحمد، وحدة A1 متاحة"
    with pytest.raises(ValidationFailed) as exc:
        render_template(body, {"name": "أحمد"})
    assert exc.value.details["missing"] == ["unit"]


async def test_template_send_requires_approved_and_goes_through_pipeline(db, tenant, owner_ctx):
    async with db.begin():
        await create_template(
            db, tenant_id=tenant.id, name="unit_offer",
            body="أهلاً {{name}}! {{unit}} في {{area}} بسعر {{price}} — متاحة للمعاينة.",
            status="approved",
        )
        person = Person(tenant_id=tenant.id, full_name="عميل قوالب", phone="+201666666666")
        db.add(person)
        await db.flush()
        from app.conversations.service import get_or_open_conversation

        conv, _ = await get_or_open_conversation(
            db, tenant_id=tenant.id, person_id=person.id, channel="whatsapp",
            provider="simulator",
        )
        conv_id = conv.id

    # rejected template → blocked
    async with db.begin():
        await create_template(
            db, tenant_id=tenant.id, name="rejected_tpl", body="سلام {{x}}",
            status="rejected",
        )
        with pytest.raises(Conflict):
            await send_templated_message(
                db, tenant_id=tenant.id, conversation_id=conv_id,
                template_name="rejected_tpl", variables={"x": "1"},
            )
    # approved template sends through the consent-gated pipeline
    async with db.begin():
        result = await send_templated_message(
            db, tenant_id=tenant.id, conversation_id=conv_id,
            template_name="unit_offer",
            variables={"name": "عميل قوالب", "unit": "A1", "area": "التجمع", "price": "4,000,000"},
        )
    assert result["status"] == "queued"
    from app.conversations.models import Message

    msg = (
        await db.execute(
            select(Message).where(Message.id == uuid.UUID(result["message_id"]))
        )
    ).scalar_one()
    assert "أهلاً عميل قوالب!" in msg.text
    assert "{{" not in msg.text


# ---------- media storage ----------
async def test_media_upload_local_backend(api, db, tenant):
    res = await api.post(
        "/api/v1/ops/media",
        files={"file": ("unit.jpg", b"\xff\xd8\xff\xe0-fake-jpeg", "image/jpeg")},
        headers={"X-Dev-Email": "owner@acme.test"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["path"].startswith("local://")
    assert body["size"] > 0


# ---------- offer document ----------
async def test_offer_document_generated_from_snapshot(db, tenant, owner_ctx):
    from app.sales.service import generate_offer_document

    async with db.begin():
        project = await create_project(db, tenant_id=tenant.id, name="Doc Compound")
        asset = await create_asset(
            db, tenant_id=tenant.id, title="Doc Unit", property_type="apartment",
            asset_type="unit", project_id=project.id,
        )
        await set_price(db, tenant_id=tenant.id, asset_id=asset.id, amount="4000000")
        person = Person(tenant_id=tenant.id, full_name="مشتري مستند", phone="+201555555555")
        db.add(person)
        await db.flush()
        lead = await create_lead(db, tenant_id=tenant.id, person_id=person.id, source="test")
        opp = await create_opportunity(db, tenant_id=tenant.id, lead_id=lead.id,
                                       person_id=person.id, actor_id=owner_ctx.user_id)
        offer = await create_offer(
            db, tenant_id=tenant.id, opportunity_id=opp.id, asset_id=asset.id,
            price_amount=__import__("decimal").Decimal("4050000"), actor_id=owner_ctx.user_id,
        )
        result = await generate_offer_document(
            db, tenant_id=tenant.id, offer_id=offer.id, actor_id=owner_ctx.user_id,
        )
    assert result["html_length"] > 500
    doc = await db.get(__import__("app.finance.models", fromlist=["Document"]).Document,
                       uuid.UUID(result["document_id"]))
    assert doc.kind == "offer"
    assert "4,050,000" in doc.metadata_["html"]


# ---------- retention job ----------
async def test_retention_cleans_stale_counters(db):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text as _text

    async with db.begin():
        # a STALE window (3h old) must be swept; fresh windows survive
        stale_id = uuid.uuid4()
        await db.execute(
            _text("""INSERT INTO rate_limit_counters (id, bucket_key, window_start, count)
                     VALUES (:id, :key, :ws, 5)"""),
            {"id": stale_id, "key": f"tenant:{uuid.uuid4()}:x",
             "ws": datetime.now(UTC) - timedelta(hours=3)},
        )
    from app.jobs.ops_api import run_retention

    async with db.begin():
        stats = await run_retention(db)
    assert stats["rate_counters"] >= 1
    remaining = (
        await db.execute(_text("SELECT count(*) FROM rate_limit_counters WHERE id = :id"),
                         {"id": stale_id})
    ).scalar_one()
    assert remaining == 0



from app.leads.service import create_lead  # noqa: E402
from app.properties.service import (  # noqa: E402
    create_asset,
    create_project,
    set_price,
)
from app.sales.service import create_offer, create_opportunity  # noqa: E402
