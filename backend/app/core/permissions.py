"""RBAC permissions (§93).

Permission format: "<resource>:<action>", e.g. "leads:write".
Authorization = Role + Branch/Team scope + Resource ownership + Explicit permission.
Roles are tenant-scoped; system roles are seeded at provisioning.
"""

from __future__ import annotations

from fastapi import Depends

from app.core import tenancy
from app.core.security import get_auth

# --- Canonical permission strings ---
PEOPLE_READ = "people:read"
PEOPLE_WRITE = "people:write"
LEADS_READ = "leads:read"
LEADS_WRITE = "leads:write"
CONVERSATIONS_READ = "conversations:read"
CONVERSATIONS_WRITE = "conversations:write"
PROPERTIES_READ = "properties:read"
PROPERTIES_WRITE = "properties:write"
INVENTORY_READ = "inventory:read"
INVENTORY_WRITE = "inventory:write"
LISTINGS_READ = "listings:read"
LISTINGS_WRITE = "listings:write"
OPPORTUNITIES_READ = "opportunities:read"
OPPORTUNITIES_WRITE = "opportunities:write"
VIEWINGS_READ = "viewings:read"
VIEWINGS_WRITE = "viewings:write"
OFFERS_READ = "offers:read"
OFFERS_WRITE = "offers:write"
OFFERS_APPROVE = "offers:approve"
RESERVATIONS_READ = "reservations:read"
RESERVATIONS_WRITE = "reservations:write"
CONTRACTS_READ = "contracts:read"
CONTRACTS_WRITE = "contracts:write"
FINANCE_READ = "finance:read"
FINANCE_WRITE = "finance:write"
DEALS_READ = "deals:read"
DEALS_WRITE = "deals:write"
MARKETING_READ = "marketing:read"
MARKETING_WRITE = "marketing:write"
AUTOMATION_READ = "automation:read"
AUTOMATION_WRITE = "automation:write"
AI_READ = "ai:read"
AI_RUN = "ai:run"
ANALYTICS_READ = "analytics:read"
TEAM_READ = "team:read"
TEAM_WRITE = "team:write"
SETTINGS_READ = "settings:read"
SETTINGS_WRITE = "settings:write"
AUDIT_READ = "audit:read"
IMPORT_RUN = "import:run"

ALL_PERMISSIONS: list[str] = [v for k, v in sorted(globals().items()) if k.isupper() and k.startswith(("PEOPLE","LEADS","CONVERSATIONS","PROPERTIES","INVENTORY","LISTINGS","OPPORTUNITIES","VIEWINGS","OFFERS","RESERVATIONS","CONTRACTS","FINANCE","DEALS","MARKETING","AUTOMATION","AI_","ANALYTICS","TEAM","SETTINGS","AUDIT","IMPORT"))]

# --- System roles (seeded per tenant at provisioning, idempotent) ---
SYSTEM_ROLES: dict[str, list[str]] = {
    "owner": ALL_PERMISSIONS,
    "admin": [p for p in ALL_PERMISSIONS if p != "settings:write" or True],  # admin = owner minus billing actions (future)
    "sales_manager": [
        LEADS_READ, LEADS_WRITE, PEOPLE_READ, PEOPLE_WRITE, CONVERSATIONS_READ, CONVERSATIONS_WRITE,
        PROPERTIES_READ, INVENTORY_READ, INVENTORY_WRITE, LISTINGS_READ,
        OPPORTUNITIES_READ, OPPORTUNITIES_WRITE, VIEWINGS_READ, VIEWINGS_WRITE,
        OFFERS_READ, OFFERS_WRITE, OFFERS_APPROVE, RESERVATIONS_READ, RESERVATIONS_WRITE,
        DEALS_READ, DEALS_WRITE, MARKETING_READ, AUTOMATION_READ, AI_READ, AI_RUN,
        ANALYTICS_READ, TEAM_READ, AUDIT_READ, CONTRACTS_READ, CONTRACTS_WRITE, FINANCE_READ,
    ],
    "sales": [
        LEADS_READ, LEADS_WRITE, PEOPLE_READ, PEOPLE_WRITE, CONVERSATIONS_READ, CONVERSATIONS_WRITE,
        PROPERTIES_READ, INVENTORY_READ, LISTINGS_READ,
        OPPORTUNITIES_READ, OPPORTUNITIES_WRITE, VIEWINGS_READ, VIEWINGS_WRITE,
        OFFERS_READ, OFFERS_WRITE, RESERVATIONS_READ, RESERVATIONS_WRITE,
        AI_READ, AI_RUN,
    ],
    "marketing": [
        LEADS_READ, LEADS_WRITE, PEOPLE_READ, MARKETING_READ, MARKETING_WRITE,
        PROPERTIES_READ, LISTINGS_READ, LISTINGS_WRITE, ANALYTICS_READ, AI_READ, AI_RUN, AUTOMATION_READ, AUTOMATION_WRITE, IMPORT_RUN,
    ],
    "finance": [
        DEALS_READ, DEALS_WRITE, FINANCE_READ, FINANCE_WRITE, CONTRACTS_READ, CONTRACTS_WRITE,
        OFFERS_READ, RESERVATIONS_READ, ANALYTICS_READ, PEOPLE_READ, PROPERTIES_READ, AUDIT_READ,
    ],
    "operations": [
        PROPERTIES_READ, PROPERTIES_WRITE, INVENTORY_READ, INVENTORY_WRITE, LISTINGS_READ, LISTINGS_WRITE,
        VIEWINGS_READ, VIEWINGS_WRITE, PEOPLE_READ, LEADS_READ, ANALYTICS_READ, AI_READ, IMPORT_RUN,
    ],
    "viewer": [LEADS_READ, PEOPLE_READ, PROPERTIES_READ, LISTINGS_READ, ANALYTICS_READ, AI_READ],
    "external_partner": [LISTINGS_READ, PROPERTIES_READ, LEADS_READ],
}


def check(auth: tenancy.AuthContext, permission: str) -> None:
    """Pure authorization check (usable from services, tests, and dependencies)."""
    auth.require(permission)


def require(permission: str):  # noqa: ANN201 — FastAPI dependency factory
    """Dependency factory: authenticate (full chain) then require a permission."""

    async def _dep(auth: tenancy.AuthContext = Depends(get_auth)) -> tenancy.AuthContext:
        check(auth, permission)
        return auth

    return _dep
