"""Tenant & auth context (§1.9, §103).

Every request resolves: who is calling, for which tenant, with what role scope.
Application-layer authorization happens here; RLS is the second wall.

Request-scoped state uses ContextVars so concurrent requests never leak
identity into each other.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.core.db import current_tenant_id
from app.core.errors import NotAuthenticated


@dataclass(frozen=True)
class AuthContext:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID | None = None
    branch_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    role_key: str = "viewer"
    permissions: frozenset[str] = field(default_factory=frozenset)
    is_platform_admin: bool = False
    email: str = ""

    def has(self, permission: str) -> bool:
        if self.is_platform_admin:
            return True
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if not self.has(permission):
            from app.core.errors import PermissionDenied

            raise PermissionDenied(f"Missing permission: {permission}")


_auth_ctx: ContextVar[AuthContext | None] = ContextVar("auth_ctx", default=None)


def bind_auth(ctx: AuthContext) -> None:
    """Bind auth to this request (async context) and set the tenant GUC scope."""
    current_tenant_id.set(ctx.tenant_id)
    _auth_ctx.set(ctx)


def current_auth() -> AuthContext:
    ctx = _auth_ctx.get()
    if ctx is None:
        raise NotAuthenticated("Request not authenticated")
    return ctx


async def current_auth_dep() -> AuthContext:
    """FastAPI dependency wrapper around current_auth."""
    return current_auth()


def current_tenant() -> uuid.UUID:
    return current_auth().tenant_id


def clear_auth() -> None:
    """Test helper: reset the context for the current async context."""
    _auth_ctx.set(None)
    current_tenant_id.set(None)
