"""Channel API: webhooks (Meta), simulator (dev), accounts, send message."""

from __future__ import annotations

import hmac
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.gateway import InboundMessage
from app.channels.models import ChannelAccount, WebhookEvent
from app.channels.service import (
    connect_channel_account,
    persist_webhook,
    process_inbound_message,
    send_outbound_message,
)
from app.core.config import settings
from app.core.db import get_session
from app.core.errors import NotFound, ValidationFailed
from app.core.permissions import CONVERSATIONS_READ, CONVERSATIONS_WRITE, SETTINGS_WRITE, require
from app.core.tenancy import AuthContext
from app.events.queue import enqueue

router = APIRouter(prefix="/channels", tags=["channels"])


# ---------- Meta WhatsApp webhook (§49: verify → persist raw → async) ----------
@router.get("/webhooks/meta-whatsapp")
async def meta_verify(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    """Meta webhook subscription handshake — verify token is deployment config."""
    from app.core.config import settings as cfg

    if hub_mode == "subscribe" and hmac.compare_digest(
        hub_verify_token or "", cfg.meta_webhook_verify_token
    ):
        return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge
    raise ValidationFailed("Webhook verification failed")


@router.post("/webhooks/meta-whatsapp")
async def meta_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    """Meta webhook (V4-hardened multi-tenant routing):

    parse → phone_number_id → EXACT ChannelAccount (decrypted) → tenant
    → signature verification (fail-closed) → persist raw → dedup → async.
    """

    from app.channels.meta_whatsapp import MetaWhatsAppAdapter
    from app.core.crypto import decrypt_config

    body_bytes = await request.body()
    payload = await request.json()
    signature = request.headers.get("X-Hub-Signature-256")

    # 1) normalize FIRST — the payload itself carries the routing key
    normalized = MetaWhatsAppAdapter.parse_webhook(payload)
    if not normalized:
        await persist_webhook(session, provider="meta_whatsapp", channel="whatsapp",
                              payload=payload, signature=signature, tenant_id=None)
        return {"status": "ignored"}
    phone_number_id = normalized[0].get("phone_number_id")

    # 2) exact account by decrypted phone_number_id — never "any connected account"
    accounts = (
        await session.execute(
            select(ChannelAccount).where(
                ChannelAccount.provider == "meta_whatsapp",
                ChannelAccount.status == "connected",
            )
        )
    ).scalars().all()
    account = None
    for a in accounts:
        cfg = decrypt_config(a.config or {})
        if cfg.get("phone_number_id") == phone_number_id:
            account = a
            break
    if account is None:
        webhook, _ = await persist_webhook(
            session, provider="meta_whatsapp", channel="whatsapp", payload=payload,
            signature=signature, tenant_id=None,
        )
        webhook.status = "failed"
        webhook.error = "no connected account matches phone_number_id"
        raise ValidationFailed("No connected WhatsApp account for this webhook")
    tenant_id = account.tenant_id

    # 3) signature verification — FAIL-CLOSED, against THIS account's secret
    if not account.config.get("app_secret") and "_encrypted" not in (account.config or {}):
        webhook, _ = await persist_webhook(
            session, provider="meta_whatsapp", channel="whatsapp", payload=payload,
            signature=signature, tenant_id=tenant_id,
        )
        webhook.status = "failed"
        webhook.error = "account missing app_secret"
        raise ValidationFailed("Meta account configured without app_secret")
    secret = decrypt_config(account.config or {}).get("app_secret", "")
    if not MetaWhatsAppAdapter.verify_webhook_signature(body_bytes, signature or "", secret):
        webhook, _ = await persist_webhook(
            session, provider="meta_whatsapp", channel="whatsapp", payload=payload,
            signature=signature, tenant_id=tenant_id,
        )
        webhook.status = "failed"
        webhook.error = "invalid signature"
        raise ValidationFailed("Invalid webhook signature")

    # 4) dedup + persist raw + enqueue async processing
    webhook, is_new = await persist_webhook(
        session, provider="meta_whatsapp", channel="whatsapp", payload=payload,
        signature=signature, tenant_id=tenant_id,
    )
    if not is_new:
        return {"status": "duplicate"}

    webhook.status = "processing"
    await enqueue(
        session,
        job_type="channel.process_webhook",
        tenant_id=tenant_id,
        payload={"webhook_id": str(webhook.id), "messages": normalized},
        unique_key=f"webhook-{webhook.id}",
    )
    return {"status": "accepted"}


# ---------- Simulator (dev/testing — same pipeline as Meta) ----------
class SimulatorInbound(BaseModel):
    tenant_slug: str
    from_phone: str
    name: str | None = None
    text: str = ""
    message_type: str = "text"
    external_id: str | None = None


@router.post("/simulator/inbound")
async def simulator_inbound(
    body: SimulatorInbound, session: AsyncSession = Depends(get_session),
    x_cron_secret: str = Header(default=""),
):
    if settings.is_production and not settings.test_mode:
        raise ValidationFailed("Simulator disabled (enable TEST_MODE for QA environments)")
    if settings.is_production and not hmac.compare_digest(
        x_cron_secret, settings.cron_secret
    ):
        # prod-QA mode still must not be an open cross-tenant injection endpoint
        raise ValidationFailed("Simulator requires a valid X-Cron-Secret header")
    from app.organizations.models import Tenant

    tenant = (
        await session.execute(select(Tenant).where(Tenant.slug == body.tenant_slug))
    ).scalar_one_or_none()
    if tenant is None:
        raise NotFound(f"Tenant {body.tenant_slug!r} not found")

    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"messages": [{"id": body.external_id, "from": body.from_phone,
                                                          "type": body.message_type,
                                                          "text": {"body": body.text}}]}}]}],
    }
    webhook, is_new = await persist_webhook(
        session, provider="simulator", channel="whatsapp", payload=payload,
        tenant_id=tenant.id, fingerprint=body.external_id,
    )
    if not is_new:
        return {"status": "duplicate"}
    result = await process_inbound_message(
        session, tenant_id=tenant.id,
        msg=InboundMessage(
            channel="whatsapp", provider="simulator", external_id=body.external_id,
            person_ref=body.from_phone, person_name=body.name, message_type=body.message_type,
            text=body.text, raw_event_ref=str(webhook.id),
        ),
    )
    webhook.status = "processed"
    return {"status": "ok", **result}


# ---------- Accounts (tenant provider connections) ----------
class AccountIn(BaseModel):
    channel: str
    provider: str
    display_name: str | None = None
    config: dict[str, Any] = {}


@router.post("/accounts", status_code=201)
async def create_account(
    body: AccountIn,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    account = await connect_channel_account(
        session, tenant_id=auth.tenant_id, channel=body.channel, provider=body.provider,
        config=body.config, display_name=body.display_name, actor_id=auth.user_id,
    )
    return {"id": str(account.id), "channel": account.channel, "provider": account.provider,
            "status": account.status}


@router.get("/accounts")
async def list_accounts(
    auth: AuthContext = Depends(require(CONVERSATIONS_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(ChannelAccount).where(ChannelAccount.tenant_id == auth.tenant_id)
        )
    ).scalars().all()
    return [
        {"id": str(a.id), "channel": a.channel, "provider": a.provider,
         "display_name": a.display_name, "status": a.status}
        for a in rows
    ]


# ---------- Send message (outbound intent) ----------
class SendIn(BaseModel):
    conversation_id: uuid.UUID
    text: str | None = None
    message_type: str = "text"
    media_url: str | None = None


@router.post("/send")
async def send_message(
    body: SendIn,
    auth: AuthContext = Depends(require(CONVERSATIONS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if not body.text and not body.media_url:
        raise ValidationFailed("text or media_url required")
    # §98-100 gates: monthly message quota + per-tenant rate limit
    from app.analytics.billing import check_quota, record_usage
    from app.core.ratelimit import check_rate_limit

    await check_quota(session, tenant_id=auth.tenant_id, kind="messages")
    await check_rate_limit(
        session, key=f"tenant:{auth.tenant_id}:send", limit=60, window_seconds=60
    )
    result = await send_outbound_message(
        session, tenant_id=auth.tenant_id, conversation_id=body.conversation_id,
        sender_type="user", sender_id=str(auth.user_id), text=body.text,
        message_type=body.message_type, media_url=body.media_url,
        idempotency_ref=request_id(auth),
    )
    await record_usage(session, tenant_id=auth.tenant_id, kind="messages")
    return result


def request_id(auth: AuthContext) -> str:
    return f"api-{auth.user_id}-{uuid.uuid4().hex[:8]}"


@router.get("/webhooks/recent")
async def recent_webhooks(
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(WebhookEvent)
            .where(WebhookEvent.tenant_id == auth.tenant_id)
            .order_by(WebhookEvent.received_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [
        {"id": str(w.id), "provider": w.provider, "status": w.status,
         "received_at": w.received_at.isoformat(), "error": w.error}
        for w in rows
    ]


# ---------- Message templates (Meta WhatsApp requirement) ----------
class TemplateIn(BaseModel):
    name: str
    body: str
    language: str = "ar"
    category: str = "UTILITY"


class TemplateSendIn(BaseModel):
    conversation_id: uuid.UUID
    template_name: str
    variables: dict[str, Any] = {}
    language: str = "ar"


@router.post("/templates", status_code=201)
async def post_template(
    body: TemplateIn,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.channels.templates import create_template

    template = await create_template(
        session, tenant_id=auth.tenant_id, name=body.name, body=body.body,
        language=body.language, category=body.category,
        created_by=str(auth.user_id),
    )
    return {"id": str(template.id), "name": template.name,
            "variables": template.variables, "status": template.status}


@router.get("/templates")
async def list_templates(
    auth: AuthContext = Depends(require(CONVERSATIONS_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    from app.channels.models import MessageTemplate

    rows = (
        await session.execute(
            select(MessageTemplate).where(MessageTemplate.tenant_id == auth.tenant_id)
        )
    ).scalars().all()
    return [
        {"id": str(t.id), "name": t.name, "language": t.language, "category": t.category,
         "status": t.status, "variables": t.variables, "body": t.body}
        for t in rows
    ]


@router.post("/templates/send")
async def send_template(
    body: TemplateSendIn,
    auth: AuthContext = Depends(require(CONVERSATIONS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.channels.templates import send_templated_message

    return await send_templated_message(
        session, tenant_id=auth.tenant_id, conversation_id=body.conversation_id,
        template_name=body.template_name, variables=body.variables,
        language=body.language, sender_type="user", actor_id=auth.user_id,
    )
