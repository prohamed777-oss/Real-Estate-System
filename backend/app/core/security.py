"""Authentication.

Two modes, same AuthContext result:
1. Production: Supabase Auth JWT verification (JWKS preferred, HS256 secret fallback).
2. Development/tests: X-Dev-Email header (only when settings.auth_dev_enabled),
   which resolves a seeded local user.

Security boundary (§103):
    Authentication -> Tenant Resolution -> Authorization -> Resource Auth -> Domain -> DB
"""

from __future__ import annotations

import uuid
from typing import Any

import jwt
from fastapi import Depends, Request
from jwt import PyJWKClient

from app.core import tenancy
from app.core.config import settings
from app.core.errors import NotAuthenticated

_jwk_client: PyJWKClient | None = None


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        url = settings.jwks_url
        if not url:
            raise NotAuthenticated("Supabase JWKS is not configured")
        _jwk_client = PyJWKClient(url, cache_keys=True, lifespan=3600)
    return _jwk_client


def decode_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token and return its claims."""
    audience = "authenticated"
    if settings.supabase_jwt_secret:
        # Legacy symmetric verification
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience=audience,
            options={"verify_aud": True},
        )
        return claims
    signing_key = _get_jwk_client().get_signing_key_from_jwt(token).key
    return jwt.decode(
        token, signing_key, algorithms=["RS256", "ES256"], audience=audience, options={"verify_aud": True}
    )


async def resolve_user_context(claims: dict[str, Any]) -> tenancy.AuthContext:
    """Load the user's membership and role permissions from the DB.

    The token proves identity; the database defines what that identity may do.
    """
    from sqlalchemy import select

    from app.organizations.models import Membership, Role, RolePermission, User

    sub = claims.get("sub")
    if not sub:
        raise NotAuthenticated("Token has no subject")
    user_id = uuid.UUID(sub)

    async with __import__("app.core.db", fromlist=["session_factory"]).session_factory() as session:
        async with session.begin():
            user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if user is None or not user.is_active:
                raise NotAuthenticated("Unknown or inactive user")
            membership = (
                await session.execute(
                    select(Membership).where(
                        Membership.user_id == user_id,
                        Membership.tenant_id == user.tenant_id,
                    )
                )
            ).scalar_one_or_none()
            role_permissions: set[str] = set()
            role_key = "viewer"
            if membership:
                role = (await session.execute(select(Role).where(Role.id == membership.role_id))).scalar_one_or_none()
                if role:
                    role_key = role.key
                    role_permissions = set(
                        (await session.execute(
                            select(RolePermission.permission).where(RolePermission.role_id == role.id)
                        )).scalars()
                    )
            return tenancy.AuthContext(
                user_id=user_id,
                tenant_id=user.tenant_id,
                organization_id=membership.organization_id if membership else None,
                branch_id=membership.branch_id if membership else None,
                team_id=membership.team_id if membership else None,
                role_key=role_key,
                permissions=frozenset(role_permissions),
                is_platform_admin=user.is_platform_admin,
                email=user.email or "",
            )


async def get_auth(request: Request) -> tenancy.AuthContext:
    """FastAPI dependency: authenticate and bind the request context."""
    # Dev bypass (never in production)
    if settings.auth_dev_enabled and not settings.is_production:
        dev_email = request.headers.get("X-Dev-Email")
        if dev_email:
            claims = {"sub": await _dev_sub_for_email(dev_email)}
            ctx = await resolve_user_context(claims)
            tenancy.bind_auth(ctx)
            return ctx

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise NotAuthenticated("Missing bearer token")
    claims = decode_token(auth_header.removeprefix("Bearer ").strip())
    ctx = await resolve_user_context(claims)
    tenancy.bind_auth(ctx)
    return ctx


async def _dev_sub_for_email(email: str) -> str:
    from sqlalchemy import select

    from app.core.db import session_factory
    from app.organizations.models import User

    async with session_factory() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        raise NotAuthenticated(f"Dev user not found: {email}")
    return str(user.id)


Auth = Depends(get_auth)
