"""Organization/branch/team/membership/role management + audit log API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.db import get_session
from app.core.errors import NotFound, ValidationFailed
from app.core.pagination import CursorPage, encode_cursor
from app.core.permissions import AUDIT_READ, SETTINGS_WRITE, TEAM_READ, TEAM_WRITE, require
from app.core.tenancy import AuthContext
from app.organizations.models import (
    AuditLog,
    Branch,
    Membership,
    Organization,
    Role,
    RolePermission,
    Team,
    User,
)
from app.organizations.provisioning import get_or_create_user

router = APIRouter(tags=["organization"])


# ---------- schemas ----------
class OrganizationIn(BaseModel):
    name: str
    legal_name: str | None = None
    timezone: str = "Africa/Cairo"
    currency: str = "EGP"
    locale: str = "ar"


class BranchIn(BaseModel):
    name: str
    code: str | None = None
    organization_id: uuid.UUID
    timezone: str = "Africa/Cairo"
    address: dict[str, Any] = {}


class TeamIn(BaseModel):
    name: str
    organization_id: uuid.UUID
    branch_id: uuid.UUID | None = None
    department: str = "sales"


class MembershipIn(BaseModel):
    email: EmailStr
    full_name: str = ""
    role_key: str
    organization_id: uuid.UUID
    branch_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None


# ---------- organizations ----------
@router.get("/org/organizations")
async def list_organizations(
    auth: AuthContext = Depends(require(TEAM_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (await session.execute(select(Organization).where(Organization.tenant_id == auth.tenant_id))).scalars()
    return [
        {"id": str(o.id), "name": o.name, "legal_name": o.legal_name, "timezone": o.timezone,
         "currency": o.currency, "locale": o.locale, "status": o.status}
        for o in rows
    ]


@router.post("/org/organizations", status_code=201)
async def create_organization(
    body: OrganizationIn,
    auth: AuthContext = Depends(require(SETTINGS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    org = Organization(tenant_id=auth.tenant_id, **body.model_dump())
    session.add(org)
    await session.flush()
    await audit(session, tenant_id=auth.tenant_id, actor_type="user", actor_id=auth.user_id,
                action="organization.created", entity_type="organization", entity_id=org.id,
                after={"name": org.name})
    return {"id": str(org.id), "name": org.name}


# ---------- branches ----------
@router.get("/org/branches")
async def list_branches(
    auth: AuthContext = Depends(require(TEAM_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (await session.execute(select(Branch).where(Branch.tenant_id == auth.tenant_id))).scalars()
    return [{"id": str(b.id), "name": b.name, "code": b.code, "organization_id": str(b.organization_id),
             "is_active": b.is_active} for b in rows]


@router.post("/org/branches", status_code=201)
async def create_branch(
    body: BranchIn,
    auth: AuthContext = Depends(require(TEAM_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    branch = Branch(tenant_id=auth.tenant_id, **body.model_dump())
    session.add(branch)
    await session.flush()
    await audit(session, tenant_id=auth.tenant_id, actor_type="user", actor_id=auth.user_id,
                action="branch.created", entity_type="branch", entity_id=branch.id, after={"name": branch.name})
    return {"id": str(branch.id), "name": branch.name}


# ---------- teams ----------
@router.get("/org/teams")
async def list_teams(
    auth: AuthContext = Depends(require(TEAM_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (await session.execute(select(Team).where(Team.tenant_id == auth.tenant_id))).scalars()
    return [{"id": str(t.id), "name": t.name, "department": t.department,
             "branch_id": str(t.branch_id) if t.branch_id else None} for t in rows]


@router.post("/org/teams", status_code=201)
async def create_team(
    body: TeamIn,
    auth: AuthContext = Depends(require(TEAM_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    team = Team(tenant_id=auth.tenant_id, **body.model_dump())
    session.add(team)
    await session.flush()
    return {"id": str(team.id), "name": team.name}


# ---------- users & memberships ----------
@router.get("/org/users")
async def list_users(
    auth: AuthContext = Depends(require(TEAM_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(User, Membership, Role)
        .join(Membership, Membership.user_id == User.id, isouter=True)
        .join(Role, Role.id == Membership.role_id, isouter=True)
        .where(User.tenant_id == auth.tenant_id)
    )).all()
    out = []
    for user, membership, role in rows:
        out.append({
            "id": str(user.id), "email": user.email, "full_name": user.full_name,
            "is_active": user.is_active,
            "role": role.key if role else None,
            "branch_id": str(membership.branch_id) if membership and membership.branch_id else None,
            "team_id": str(membership.team_id) if membership and membership.team_id else None,
        })
    return out


@router.post("/org/memberships", status_code=201)
async def assign_membership(
    body: MembershipIn,
    auth: AuthContext = Depends(require(TEAM_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    role = (
        await session.execute(
            select(Role).where(Role.tenant_id == auth.tenant_id, Role.key == body.role_key)
        )
    ).scalar_one_or_none()
    if role is None:
        raise ValidationFailed(f"Unknown role: {body.role_key}")
    user = await get_or_create_user(
        session, tenant_id=auth.tenant_id, email=body.email, full_name=body.full_name
    )
    existing = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user.id, Membership.organization_id == body.organization_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.role_id = role.id
        existing.branch_id = body.branch_id
        existing.team_id = body.team_id
        membership = existing
    else:
        membership = Membership(
            tenant_id=auth.tenant_id, user_id=user.id, organization_id=body.organization_id,
            branch_id=body.branch_id, team_id=body.team_id, role_id=role.id,
        )
        session.add(membership)
    await session.flush()
    await audit(session, tenant_id=auth.tenant_id, actor_type="user", actor_id=auth.user_id,
                action="auth.role_changed", entity_type="user", entity_id=user.id,
                after={"role": role.key, "email": user.email})
    return {"membership_id": str(membership.id), "user_id": str(user.id), "role": role.key}


@router.delete("/org/memberships/{membership_id}", status_code=204)
async def remove_membership(
    membership_id: uuid.UUID,
    auth: AuthContext = Depends(require(TEAM_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    membership = (
        await session.execute(
            select(Membership).where(Membership.id == membership_id, Membership.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFound("Membership not found")
    await session.delete(membership)


# ---------- roles & permissions ----------
@router.get("/org/roles")
async def list_roles(
    auth: AuthContext = Depends(require(TEAM_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(Role, RolePermission.permission)
        .join(RolePermission, RolePermission.role_id == Role.id, isouter=True)
        .where(Role.tenant_id == auth.tenant_id)
    )).all()
    by_role: dict[str, dict[str, Any]] = {}
    for role, perm in rows:
        entry = by_role.setdefault(role.key, {"key": role.key, "name": role.name, "permissions": []})
        if perm:
            entry["permissions"].append(perm)
    return list(by_role.values())


# ---------- audit ----------
@router.get("/audit")
async def list_audit(
    auth: AuthContext = Depends(require(AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, le=200),
    cursor: str | None = None,
    entity_type: str | None = None,
    action: str | None = None,
) -> CursorPage:
    q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == auth.tenant_id)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
    )
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if action:
        q = q.where(AuditLog.action == action)
    rows = (await session.execute(q.limit(limit))).scalars().all()
    items = [
        {"id": str(r.id), "actor_type": r.actor_type, "actor_id": r.actor_id, "action": r.action,
         "entity_type": r.entity_type, "entity_id": r.entity_id, "before": r.before, "after": r.after,
         "source": r.source, "occurred_at": r.occurred_at.isoformat()}
        for r in rows
    ]
    next_cursor = None
    if len(items) == limit:
        last = rows[-1]
        next_cursor = encode_cursor(created_at=last.occurred_at, id_=last.id)
    return CursorPage(items=items, next_cursor=next_cursor, has_more=bool(next_cursor))
