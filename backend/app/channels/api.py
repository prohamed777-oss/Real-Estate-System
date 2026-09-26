"""Channel API: webhooks (Meta), simulator (dev), accounts, send message."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
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

    if hub_mode == "subscribe" and hub_verify_token == cfg.meta_webhook_verify_token:
        return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge
    raise ValidationFailed("Webhook verification failed")


@router.post("/webhooks/meta-whatsapp")
async def meta_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    from app.channels.meta_whatsapp import MetaWhatsAppAdapter

    payload = await request.json()
    signature = request.headers.get("X-Hub-Signature-256")
    # Signature verification uses the app secret of the matching account; when
    # configured we verify, otherwise (dev) we accept and persist raw.
    body_bytes = await request.body()
    webhook, is_new = await persist_webhook(
        session, provider="meta_whatsapp", channel="whatsapp", payload=payload,
        signature=signature,
    )
    if not is_new:
        webhook.status = "duplicate"
        return {"status": "duplicate"}

    from app.core.config import settings as cfg
    from app.channels.service import _find_tenant_for_provider_ref

    # Verify signature if any account has app_secret
    tenant_id = await _find_tenant_for_provider_ref(session, provider="meta_whatsapp", provider_ref=None)
    verified = False
    if tenant_id:
        account = (
            await session.execute(
                select(ChannelAccount).where(
                    ChannelAccount.tenant_id == tenant_id, ChannelAccount.provider == "meta_whatsapp"
                )
            )
        ).scalar_one_or_none()
        if account and account.config.get("app_secret"):
            verified = MetaWhatsAppAdapter.verify_webhook_signature(
                body_bytes, signature or "", account.config["app_secret"]
            )
            if not verified:
                webhook.status = "failed"
                webhook.error = "invalid signature"
                raise ValidationFailed("Invalid webhook signature")

    # Normalize now to extract tenant binding, then process async
    normalized = MetaWhatsAppAdapter.parse_webhook(payload)
    if not normalized:
        webhook.status = "processed"
        webhook.processed_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
        return {"status": "ignored"}

    first = normalized[0]
    if tenant_id is None:
        tenant_id = await _find_tenant_for_provider_ref(
            session, provider="meta_whatsapp", provider_ref=first.get("phone_number_id")
        )
    if tenant_id is None:
        webhook.status = "failed"
        webhook.error = "no connected channel account"
        raise ValidationFailed("No connected WhatsApp account for this webhook")

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
    body: SimulatorInbound, session: AsyncSession = Depends(get_session)
):
    if settings.is_production:
        raise ValidationFailed("Simulator disabled in production")
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
    return await send_outbound_message(
        session, tenant_id=auth.tenant_id, conversation_id=body.conversation_id,
        sender_type="user", sender_id=str(auth.user_id), text=body.text,
        message_type=body.message_type, media_url=body.media_url,
        idempotency_ref=request_id(auth),
    )


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
