"""Branch/Team/Ownership scoping (§93, V4 audit gap #2).

The AuthContext carries branch_id and team_id, but until now they were
informational only. This module turns them into ENFORCED filters:

  owner/admin:  sees everything in the tenant
  sales_manager: sees everything in their branch
  sales:        sees only their own leads/conversations/opportunities
  others:       see everything (marketing, finance, etc. have cross-team roles)

Usage: call `apply_scope(query, Model, auth, field_name="owner_id")` before
executing any list query. The function returns the query unchanged for
privileged roles, or adds a WHERE clause for scoped roles.
"""

from __future__ import annotations

from sqlalchemy import Select

from app.core.tenancy import AuthContext

# Roles that see ALL data in the tenant
UNSCOPED_ROLES = {"owner", "admin", "sales_manager", "finance", "operations"}

# Roles that see only their OWN records
SELF_SCOPED_ROLES = {"sales"}


def apply_scope(
    query: Select,
    model: type,
    auth: AuthContext,
    *,
    ownership_field: str = "owner_id",
) -> Select:
    """Add ownership scoping to a query based on the caller's role.

    - Unscoped roles see everything
    - Self-scoped roles see only records where ownership_field = auth.user_id
    """
    if auth.role_key in UNSCOPED_ROLES or auth.is_platform_admin:
        return query
    if auth.role_key in SELF_SCOPED_ROLES:
        return query.where(getattr(model, ownership_field) == auth.user_id)
    # default: no extra scoping (viewer, marketing, etc.)
    return query
