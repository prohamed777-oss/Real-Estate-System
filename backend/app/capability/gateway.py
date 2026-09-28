"""Capability Gateway (V4 PART 10) — ONE authorization model for every
external actor: AI agents, outbound webhooks, marketplace apps, API clients.

Trust is granted BY SCOPE, scope EXPIRES, and every call is checked here —
no category of actor gets a shortcut.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capability.models import CapabilityGrant, WebhookDestination
from app.core.errors import DomainError


class GranteeType(StrEnum):
    AI_AGENT = "AI_AGENT"
    OUTBOUND_WEBHOOK = "OUTBOUND_WEBHOOK"
    MARKETPLACE_APP = "MARKETPLACE_APP"
    API_CLIENT = "API_CLIENT"


class CapabilityDenied(DomainError):
    status_code = 403
    code = "capability_denied"


class GrantExpired(DomainError):
    status_code = 403
    code = "capability_expired"


def derive_idempotency_key(execution_id: str, action: str, arguments: dict[str, Any]) -> str:
    """Principle 1.28: idempotency keys are DERIVED, not invented by callers."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{execution_id}:{action}:{canonical}".encode()).hexdigest()


async def issue_grant(
    session: AsyncSession, *, tenant_id: uuid.UUID, grantee_type: GranteeType,
    grantee_id: str, resource_scope: str, action_scope: str,
    ttl_days: int = 90, issued_by: str | None = None,
) -> CapabilityGrant:
    """Issue a capability grant. Grants ALWAYS expire (no eternal trust)."""
    grant = CapabilityGrant(
        tenant_id=tenant_id,
        grantee_type=grantee_type.value,
        grantee_id=grantee_id,
        resource_scope=resource_scope,
        action_scope=action_scope,
        issued_by=issued_by,
        expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
    )
    session.add(grant)
    await session.flush()
    return grant


async def check_capability(
    session: AsyncSession, *, tenant_id: uuid.UUID, grantee_type: GranteeType,
    grantee_id: str, resource_scope: str, action_scope: str,
) -> CapabilityGrant:
    """THE gate. Every external action passes here. Raises CapabilityDenied."""
    now = datetime.now(UTC)
    grants = (
        await session.execute(
            select(CapabilityGrant).where(
                CapabilityGrant.tenant_id == tenant_id,
                CapabilityGrant.grantee_type == grantee_type.value,
                CapabilityGrant.grantee_id == grantee_id,
                CapabilityGrant.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    matched = [
        g for g in grants
        if g.resource_scope in (resource_scope, "*")
        and action_scope in (g.action_scope, "*")
    ]
    if not matched:
        raise CapabilityDenied(
            f"No capability grant for {grantee_type.value}:{grantee_id} "
            f"→ {resource_scope}:{action_scope}",
            details={"grantee_type": grantee_type.value, "grantee_id": grantee_id,
                     "resource_scope": resource_scope, "action_scope": action_scope},
        )
    active = [g for g in matched if g.expires_at is None or g.expires_at > now]
    if not active:
        raise GrantExpired(
            "Capability grant expired — renewal required",
            details={"expired_at": max(g.expires_at for g in matched).isoformat() if matched[0].expires_at else None},
        )
    return active[0]


async def revoke_grant(session: AsyncSession, *, tenant_id: uuid.UUID,
                       grant_id: uuid.UUID, actor_id=None) -> None:
    grant = await session.get(CapabilityGrant, grant_id)
    if grant is None or grant.tenant_id != tenant_id:
        raise DomainError("Grant not found", code="not_found", status_code=404)
    grant.revoked_at = datetime.now(UTC)
    await session.flush()


# =====================================================================
# Webhook destinations: allowlist + SSRF defense (V4 PART 10.3)
# =====================================================================

import ipaddress  # noqa: E402
import socket  # noqa: E402

BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # cloud metadata endpoint
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
]


class WebhookURLRejected(DomainError):
    status_code = 400
    code = "webhook_url_rejected"


def validate_webhook_url(url: str) -> str:
    """Permanent SSRF defense: resolve the host, reject ANY private/loopback/
    metadata range — regardless of allowlist status."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise WebhookURLRejected("Only http(s) webhook URLs allowed")
    host = parsed.hostname or ""
    if not host:
        raise WebhookURLRejected("Webhook URL has no host")
    if host in ("localhost", "metadata.google.internal") or host.endswith(".internal"):
        raise WebhookURLRejected(f"Host {host!r} is forbidden")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise WebhookURLRejected(f"Cannot resolve webhook host: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        for net in BLOCKED_NETWORKS:
            if ip in net:
                raise WebhookURLRejected(
                    f"Webhook host resolves to forbidden range ({ip}) — SSRF defense"
                )
    return host


async def register_webhook_destination(
    session: AsyncSession, *, tenant_id: uuid.UUID, url: str,
    owner_user_id: uuid.UUID | None = None,
) -> tuple[WebhookDestination, str]:
    """Register → PENDING_VERIFICATION with a challenge token.
    The URL becomes VERIFIED only after the challenge is answered."""
    validate_webhook_url(url)  # permanent block, even before verification
    token = secrets.token_urlsafe(32)
    dest = WebhookDestination(
        tenant_id=tenant_id, url=url, allowlist_status="PENDING_VERIFICATION",
        verification_token=token, owner_user_id=owner_user_id,
    )
    session.add(dest)
    await session.flush()
    return dest, token


async def verify_webhook_destination(session: AsyncSession, *, tenant_id: uuid.UUID,
                                     destination_id: uuid.UUID) -> WebhookDestination:
    """Mark VERIFIED after the challenge response was validated by the caller
    (DNS TXT record containing the token, or a challenge file on the URL)."""
    dest = await session.get(WebhookDestination, destination_id)
    if dest is None or dest.tenant_id != tenant_id:
        raise DomainError("Webhook destination not found", code="not_found", status_code=404)
    if dest.allowlist_status == "BLOCKED":
        raise DomainError("Destination is blocked", code="webhook_url_rejected", status_code=400)
    validate_webhook_url(dest.url)  # re-check at verification time (DNS may change)
    dest.allowlist_status = "VERIFIED"
    dest.verified_at = datetime.now(UTC)
    await session.flush()
    return dest


async def assert_outbound_url_allowed(session: AsyncSession, *, tenant_id: uuid.UUID,
                                      url: str) -> None:
    """Called before EVERY outbound HTTP call from workflows/automations.
    Only VERIFIED destinations pass. No free-form URLs — ever."""
    validate_webhook_url(url)
    dest = (
        await session.execute(
            select(WebhookDestination).where(
                WebhookDestination.tenant_id == tenant_id,
                WebhookDestination.url == url,
                WebhookDestination.allowlist_status == "VERIFIED",
            )
        )
    ).scalar_one_or_none()
    if dest is None:
        raise CapabilityDenied(
            "Outbound URL is not a VERIFIED webhook destination for this tenant",
            details={"url": url[:200]},
        )


# =====================================================================
# Marketplace apps: install-time consent (V4 PART 10.4)
# =====================================================================

async def install_marketplace_app(
    session: AsyncSession, *, tenant_id: uuid.UUID, app_id: str,
    requested_scopes: list[str], approved_by: uuid.UUID,
) -> list[CapabilityGrant]:
    """Install = EXPLICIT consent screen equivalent: the tenant admin approves
    the exact scopes; each scope becomes its own expiring grant."""
    grants = []
    for scope in requested_scopes:
        resource, _, action = scope.partition(":")
        grants.append(await issue_grant(
            session, tenant_id=tenant_id, grantee_type=GranteeType.MARKETPLACE_APP,
            grantee_id=app_id, resource_scope=resource, action_scope=action or "*",
            ttl_days=30, issued_by=str(approved_by),
        ))
    return grants
