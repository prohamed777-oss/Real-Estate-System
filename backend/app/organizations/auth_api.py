"""Auth endpoints: production bootstrap (Supabase JWT), dev bootstrap, /me."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import NotAuthenticated
from app.core.security import decode_token, get_auth
from app.core.tenancy import AuthContext
from app.organizations.models import FeatureFlag, Membership, Role, User
from app.organizations.provisioning import provision_tenant

router = APIRouter(tags=["auth"])


class BootstrapIn(BaseModel):
    tenant_name: str = Field(min_length=2, max_length=200)
    org_name: str | None = None
    slug: str | None = None
    locale: str = "ar"
    currency: str = "EGP"
    timezone: str = "Africa/Cairo"


class DevBootstrapIn(BootstrapIn):
    owner_email: EmailStr
    owner_full_name: str = ""


@router.post("/auth/bootstrap")
async def bootstrap(
    body: BootstrapIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Production signup completion: creates a NEW tenant for the caller.

    Authorization boundary (hardened): bootstrap NEVER attaches a caller to an
    existing tenant. If the slug is taken → 409. Joining an existing tenant
    happens exclusively through explicit invitations (org/memberships API).
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise NotAuthenticated("Missing bearer token")
    claims = decode_token(auth_header.removeprefix("Bearer ").strip())
    email = claims.get("email")
    if not email:
        raise NotAuthenticated("Token has no email claim")

    from sqlalchemy import select

    from app.organizations.models import Tenant
    from app.organizations.provisioning import slugify

    slug_v = slugify(body.slug or body.tenant_name)
    existing = (
        await session.execute(select(Tenant).where(Tenant.slug == slug_v))
    ).scalar_one_or_none()
    if existing is not None:
        from app.core.errors import Conflict

        raise Conflict(
            "Tenant slug already taken — choose another name",
            details={"slug": slug_v},
        )

    tenant = await provision_tenant(
        session,
        tenant_name=body.tenant_name,
        org_name=body.org_name or body.tenant_name,
        owner_email=email,
        owner_full_name=(claims.get("user_metadata") or {}).get("full_name", ""),
        owner_user_id=uuid.UUID(claims["sub"]),
        slug=body.slug,
        locale=body.locale,
        currency=body.currency,
        timezone=body.timezone,
    )
    return {"tenant_id": str(tenant.id), "slug": tenant.slug, "status": "provisioned"}


@router.post("/dev/bootstrap", include_in_schema=False)
async def dev_bootstrap(
    body: DevBootstrapIn,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Local-dev only: provision a tenant + owner WITHOUT Supabase Auth."""
    if settings.is_production or not settings.auth_dev_enabled:
        raise NotAuthenticated("Dev bootstrap disabled")
    tenant = await provision_tenant(
        session,
        tenant_name=body.tenant_name,
        org_name=body.org_name or body.tenant_name,
        owner_email=body.owner_email,
        owner_full_name=body.owner_full_name,
        slug=body.slug,
        locale=body.locale,
        currency=body.currency,
        timezone=body.timezone,
    )
    return {"tenant_id": str(tenant.id), "slug": tenant.slug, "status": "provisioned"}


@router.get("/auth/me")
async def me(
    auth: AuthContext = Depends(get_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user = (await session.execute(select(User).where(User.id == auth.user_id))).scalar_one()
    membership = (
        await session.execute(
            select(Membership).where(Membership.user_id == auth.user_id, Membership.is_primary.is_(True))
        )
    ).scalar_one_or_none()
    role = None
    if membership:
        role = (
            await session.execute(select(Role).where(Role.id == membership.role_id))
        ).scalar_one_or_none()
    flags = {
        f.key: f.enabled
        for f in (
            await session.execute(select(FeatureFlag).where(FeatureFlag.tenant_id == auth.tenant_id))
        ).scalars()
    }
    from app.organizations.models import Tenant

    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == auth.tenant_id))
    ).scalar_one_or_none()
    return {
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "locale": user.locale},
        "tenant_id": str(auth.tenant_id),
        "tenant_slug": tenant.slug if tenant else None,
        "role": role.key if role else auth.role_key,
        "permissions": sorted(auth.permissions),
        "feature_flags": flags,
    }
