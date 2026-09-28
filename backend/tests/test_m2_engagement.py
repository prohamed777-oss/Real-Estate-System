"""M2 Engagement tests: golden inbound flow, dedup, outbound consent gate,
lead lifecycle via state machine, tasks."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.channels.gateway import InboundMessage
from app.channels.models import ChannelAccount
from app.channels.service import (
    persist_webhook,
    process_inbound_message,
    send_outbound_message,
)
from app.conversations.models import Conversation, Message, Task
from app.core.db import session_factory
from app.core.errors import Conflict, ValidationFailed
from app.events.queue import run_batch
from app.identity.models import Person
from app.leads.models import Lead
from app.leads.service import recompute_scores, transition_lead


def _msg(**kw) -> InboundMessage:
    defaults = dict(
        channel="whatsapp", provider="simulator", external_id=None, person_ref="+201000000001",
        person_name="Ahmed", message_type="text", text="عايز شقة في التجمع",
        attachments=[],
    )
    defaults.update(kw)
    return InboundMessage(**defaults)


async def test_golden_inbound_creates_person_conversation_lead(db, tenant, owner_ctx):
    async with db.begin():
        result = await process_inbound_message(db, tenant_id=tenant.id, msg=_msg())
    assert result["person_new"] is True
    assert result["conversation_new"] is True
    assert result["lead_id"] is not None

    # Second message from SAME phone: no duplicates anywhere
    async with db.begin():
        result2 = await process_inbound_message(db, tenant_id=tenant.id, msg=_msg(text="وفيه كام غرفة؟"))
    assert result2["person_new"] is False
    assert result2["conversation_new"] is False
    assert result2["lead_id"] is None  # open lead already exists

    persons = (await db.execute(
        select(__import__("sqlalchemy").func.count()).select_from(Person)
    )).scalar_one()
    conversations = (await db.execute(
        select(__import__("sqlalchemy").func.count()).select_from(Conversation)
    )).scalar_one()
    leads = (await db.execute(select(__import__("sqlalchemy").func.count()).select_from(Lead))).scalar_one()
    messages = (await db.execute(select(__import__("sqlalchemy").func.count()).select_from(Message))).scalar_one()
    assert persons == 1
    assert conversations == 1
    assert leads == 1
    assert messages == 2


async def test_webhook_dedup_by_fingerprint(db, tenant, owner_ctx):
    payload = {"entry": [{"changes": []}], "x": 1}
    async with db.begin():
        w1, new1 = await persist_webhook(db, provider="simulator", channel="whatsapp",
                                          payload=payload, tenant_id=tenant.id)
    async with db.begin():
        w2, new2 = await persist_webhook(db, provider="simulator", channel="whatsapp",
                                          payload=payload, tenant_id=tenant.id)
    assert new1 is True
    assert new2 is False
    assert w1.id == w2.id


async def test_outbound_requires_consent_gate_and_dispatches(db, tenant, owner_ctx):
    async with db.begin():
        result = await process_inbound_message(db, tenant_id=tenant.id, msg=_msg())
        conversation_id = uuid.UUID(result["conversation_id"])
        # No connected account → message stays queued, no crash
        sent = await send_outbound_message(
            db, tenant_id=tenant.id, conversation_id=conversation_id,
            sender_type="user", sender_id=str(owner_ctx.user_id), text="أهلاً بيك!",
        )
    assert sent["status"] == "queued"

    # Connect simulator account → dispatch job sends it
    async with db.begin():
        account = ChannelAccount(
            tenant_id=tenant.id, channel="whatsapp", provider="simulator",
            config={}, status="connected",
        )
        db.add(account)
    async with db.begin():
        sent2 = await send_outbound_message(
            db, tenant_id=tenant.id, conversation_id=conversation_id,
            sender_type="user", sender_id=str(owner_ctx.user_id), text="تبعتلك شقق النهارده",
        )
    async with session_factory() as s:
        async with s.begin():
            stats = await run_batch(s)
    assert stats["completed"] >= 1
    msg = (
        await db.execute(
            select(Message).where(Message.id == uuid.UUID(sent2["message_id"]))
        )
    ).scalar_one()
    assert msg.status == "sent"
    assert msg.external_id.startswith("sim-")


async def test_opt_out_blocks_marketing_but_ai_cannot_bypass(db, tenant, owner_ctx):
    from app.identity.models import CommunicationConsent

    async with db.begin():
        result = await process_inbound_message(db, tenant_id=tenant.id, msg=_msg())
        conversation_id = uuid.UUID(result["conversation_id"])
        person_id = uuid.UUID(result["person_id"])
        db.add(
            CommunicationConsent(
                tenant_id=tenant.id, person_id=person_id, channel="whatsapp",
                consent_type="marketing", status="opt_out",
            )
        )

    # AI sender with opt-out marketing consent → blocked
    async with db.begin():
        with pytest.raises(ValidationFailed):
            await send_outbound_message(
                db, tenant_id=tenant.id, conversation_id=conversation_id,
                sender_type="ai", sender_id="agent-1", text="عرض خاص!",
            )
    # AI claiming bypass → hard rejected
    async with db.begin():
        with pytest.raises(ValidationFailed):
            await send_outbound_message(
                db, tenant_id=tenant.id, conversation_id=conversation_id,
                sender_type="ai", sender_id="agent-1", text="عرض خاص!", bypass_consent=True,
            )
    # Human direct reply (transactional) is allowed despite marketing opt-out
    async with db.begin():
        ok = await send_outbound_message(
            db, tenant_id=tenant.id, conversation_id=conversation_id,
            sender_type="user", sender_id=str(owner_ctx.user_id), text="تمام، هحولك لزميلي",
        )
    assert ok["status"] == "queued"


async def test_lead_lifecycle_via_state_machine(db, tenant, owner_ctx):
    async with db.begin():
        await process_inbound_message(db, tenant_id=tenant.id, msg=_msg())
        lead = (await db.execute(select(Lead))).scalar_one()
        assert lead.lifecycle_stage == "NEW"

    async with db.begin():
        await transition_lead(db, lead=lead, event="contact", actor_type="user",
                              actor_id=owner_ctx.user_id)
        await transition_lead(db, lead=lead, event="qualify", actor_type="user",
                              actor_id=owner_ctx.user_id)
        await transition_lead(db, lead=lead, event="qualified", actor_type="user",
                              actor_id=owner_ctx.user_id)
    assert lead.lifecycle_stage == "QUALIFIED"

    # Illegal transition rejected
    async with db.begin():
        with pytest.raises(Conflict):
            await transition_lead(db, lead=lead, event="reactivate", actor_type="user")


async def test_scoring_engine_records_version_and_signals(db, tenant, owner_ctx):
    async with db.begin():
        await process_inbound_message(db, tenant_id=tenant.id, msg=_msg())
        lead = (await db.execute(select(Lead))).scalar_one()
        await recompute_scores(db, lead=lead)
    assert lead.score_version == "v1-heuristic"
    assert 0 <= lead.lead_score <= 100
    assert "calculated_at" in (lead.score_signals or {})
    assert lead.score_calculated_at is not None


async def test_task_lifecycle(db, tenant, owner_ctx):
    async with db.begin():
        task = Task(tenant_id=tenant.id, title="مكالمة متابعة", assignee_id=owner_ctx.user_id,
                    type="follow_up")
        db.add(task)
    assert task.status == "open"
    async with db.begin():
        task.status = "done"
        from datetime import UTC, datetime

        task.completed_at = datetime.now(UTC)
    assert task.completed_at is not None
