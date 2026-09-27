"""V4 API surface: capability grants, webhook destinations, custom fields,
claims (bitemporal truth), decision records."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capability.custom_fields import define_field, get_values, set_value
from app.capability.gateway import (
    GranteeType,
    assert_outbound_url_allowed,
    issue_grant,
    register_webhook_destination,
    revoke_grant,
    verify_webhook_destination,
)
from app.core.db import get_session
from app.core.permissions import (
    PROPERTIES_READ,
    PROPERTIES_WRITE,
    SETTINGS_READ,
    SETTINGS_WRITE,
    require,
)
from app.core.tenancy import AuthContext
from app.decision.services import decide

router = APIRouter(prefix="/capability", tags=["capability"])


# ---------- grants ----------
class GrantIn(BaseModel):
    grantee_type: str  # AI_AGENT | OUTBOUND_WEBHOOK | MARKETPLACE_APP | API_CLIENT
    grantee_id: str
    resource_scope: str
    action_scope: str
    ttl_days: int = 90


@router.post("/grants", status_code=201)
async def post_grant(
    body: GrantIn,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    grant = await issue_grant(
        session, tenant_id=auth.tenant_id,
        grantee_type=GranteeType(body.grantee_type), grantee_id=body.grantee_id,
        resource_scope=body.resource_scope, action_scope=body.action_scope,
        ttl_days=body.ttl_days, issued_by=str(auth.user_id),
    )
    return {"id": str(grant.id), "expires_at": grant.expires_at.isoformat()}


@router.get("/grants")
async def list_grants(
    auth: AuthContext = Depends(require(SETTINGS_READ)),
    session: AsyncSession = Depends(get_session),
    grantee_id: str | None = None,
) -> list[dict[str, Any]]:
    from app.capability.models import CapabilityGrant

    query = select(CapabilityGrant).where(CapabilityGrant.tenant_id == auth.tenant_id)
    if grantee_id:
        query = query.where(CapabilityGrant.grantee_id == grantee_id)
    rows = (await session.execute(query)).scalars().all()
    return [
        {"id": str(g.id), "grantee_type": g.grantee_type, "grantee_id": g.grantee_id,
         "resource_scope": g.resource_scope, "action_scope": g.action_scope,
         "expires_at": g.expires_at.isoformat(),
         "revoked": g.revoked_at is not None}
        for g in rows
    ]


@router.delete("/grants/{grant_id}", status_code=204)
async def delete_grant(
    grant_id: uuid.UUID,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    await revoke_grant(session, tenant_id=auth.tenant_id, grant_id=grant_id,
                       actor_id=auth.user_id)


# ---------- webhook destinations ----------
class WebhookIn(BaseModel):
    url: str


class WebhookVerifyIn(BaseModel):
    destination_id: uuid.UUID


@router.post("/webhooks", status_code=201)
async def post_webhook(
    body: WebhookIn,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    dest, token = await register_webhook_destination(
        session, tenant_id=auth.tenant_id, url=body.url, owner_user_id=auth.user_id
    )
    return {"id": str(dest.id), "status": dest.allowlist_status,
            "verification_token": token,
            "hint": "أضف الـtoken كـDNS TXT أو ملف challenge ثم اتصل بـverify"}


@router.post("/webhooks/verify")
async def post_webhook_verify(
    body: WebhookVerifyIn,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    dest = await verify_webhook_destination(
        session, tenant_id=auth.tenant_id, destination_id=body.destination_id
    )
    return {"id": str(dest.id), "status": dest.allowlist_status}


@router.post("/webhooks/check-outbound")
async def check_outbound(
    body: WebhookIn,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The exact gate every automation outbound call passes (V4 10.3)."""
    await assert_outbound_url_allowed(session, tenant_id=auth.tenant_id, url=body.url)
    return {"allowed": True}


# ---------- custom fields ----------
class FieldDefineIn(BaseModel):
    entity_type: str
    field_key: str
    field_type: str = "text"
    label: dict[str, str] = {}
    options: list = []


class FieldValueIn(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    field_key: str
    value: Any = None


@router.post("/custom-fields/define", status_code=201)
async def post_define_field(
    body: FieldDefineIn,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await define_field(
        session, tenant_id=auth.tenant_id, entity_type=body.entity_type,
        field_key=body.field_key, field_type=body.field_type, label=body.label,
        options=body.options, created_by=str(auth.user_id),
    )
    return {"id": str(row.id), "field_key": row.field_key, "field_type": row.field_type}


@router.post("/custom-fields/set")
async def post_set_value(
    body: FieldValueIn,
    auth: AuthContext = Depends(require(PROPERTIES_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await set_value(
        session, tenant_id=auth.tenant_id, entity_type=body.entity_type,
        entity_id=body.entity_id, field_key=body.field_key, value=body.value,
    )
    return {"ok": True}


@router.get("/custom-fields/{entity_type}/{entity_id}")
async def get_entity_values(
    entity_type: str,
    entity_id: uuid.UUID,
    auth: AuthContext = Depends(require(PROPERTIES_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await get_values(session, tenant_id=auth.tenant_id,
                            entity_type=entity_type, entity_id=entity_id)


# ---------- decision records ----------
class DecideIn(BaseModel):
    action: str
    subject: dict[str, Any]
    candidates: list[dict[str, Any]] = []
    objective: str = "maximize_conversion"


@router.post("/decide")
async def post_decide(
    body: DecideIn,
    auth: AuthContext = Depends(require("ai:run")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Run the Decision Plane composition (①→②→③) + record (⑤)."""
    decision = await decide(
        session, tenant_id=auth.tenant_id, action=body.action,
        subject=body.subject, candidates=body.candidates, objective=body.objective,
        context={"requested_by": str(auth.user_id)},
    )
    return {
        "decision_id": str(decision.id), "action": decision.action,
        "policy_evaluation": decision.policy_evaluation,
        "selected": decision.selected_option,
        "scoring": decision.scoring_results,
        "decision_version": decision.decision_version,
    }


@router.get("/decisions")
async def list_decisions(
    auth: AuthContext = Depends(require(SETTINGS_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    from app.decision.models import Decision

    rows = (
        await session.execute(
            select(Decision).where(Decision.tenant_id == auth.tenant_id)
            .order_by(Decision.created_at.desc()).limit(50)
        )
    ).scalars().all()
    return [
        {"id": str(d.id), "action": d.action, "selected": d.selected_option,
         "policy": d.policy_evaluation, "created_at": d.created_at.isoformat()}
        for d in rows
    ]
