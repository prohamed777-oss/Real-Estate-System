"""Document lifecycle API (§36): versions, approvals, signatures."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFound
from app.core.permissions import CONTRACTS_READ, CONTRACTS_WRITE, require
from app.core.tenancy import AuthContext
from app.finance.documents_service import (
    add_signature,
    add_version,
    create_document,
    decide_approval,
    request_approval,
    sign,
)
from app.finance.models import Document
from app.properties.models_ext import DocumentApproval, DocumentSignature, DocumentVersion

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentIn(BaseModel):
    kind: str
    title: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    storage_path: str | None = None


class VersionIn(BaseModel):
    storage_path: str | None = None
    change_note: str | None = None


class ApprovalDecisionIn(BaseModel):
    decision: str  # approved | rejected
    notes: str | None = None


class SignatureIn(BaseModel):
    signer_name: str
    role: str | None = None
    signer_person_id: uuid.UUID | None = None
    method: str = "manual"


@router.post("", status_code=201)
async def post_document(
    body: DocumentIn,
    auth: AuthContext = Depends(require(CONTRACTS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    doc = await create_document(
        session, tenant_id=auth.tenant_id, kind=body.kind, title=body.title,
        entity_type=body.entity_type, entity_id=body.entity_id,
        storage_path=body.storage_path, created_by=str(auth.user_id), actor_id=auth.user_id,
    )
    return {"id": str(doc.id), "status": doc.status, "version": doc.version}


@router.get("")
async def list_documents(
    auth: AuthContext = Depends(require(CONTRACTS_READ)),
    session: AsyncSession = Depends(get_session),
    kind: str | None = None,
) -> list[dict[str, Any]]:
    query = select(Document).where(Document.tenant_id == auth.tenant_id)
    if kind:
        query = query.where(Document.kind == kind)
    rows = (await session.execute(query.order_by(Document.created_at.desc()).limit(100))).scalars().all()
    return [
        {"id": str(d.id), "kind": d.kind, "title": d.title, "status": d.status,
         "version": d.version, "facts_verified": d.facts_verified}
        for d in rows
    ]


@router.post("/{document_id}/versions", status_code=201)
async def post_version(
    document_id: uuid.UUID,
    body: VersionIn,
    auth: AuthContext = Depends(require(CONTRACTS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    version = await add_version(
        session, tenant_id=auth.tenant_id, document_id=document_id,
        storage_path=body.storage_path, change_note=body.change_note,
        created_by=str(auth.user_id),
    )
    return {"id": str(version.id), "version": version.version}


@router.get("/{document_id}/versions")
async def get_versions(
    document_id: uuid.UUID,
    auth: AuthContext = Depends(require(CONTRACTS_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id,
                   DocumentVersion.tenant_id == auth.tenant_id)
            .order_by(DocumentVersion.version)
        )
    ).scalars().all()
    return [
        {"version": v.version, "storage_path": v.storage_path,
         "change_note": v.change_note, "created_at": v.created_at.isoformat()}
        for v in rows
    ]


@router.post("/{document_id}/approvals", status_code=201)
async def post_approval_request(
    document_id: uuid.UUID,
    auth: AuthContext = Depends(require(CONTRACTS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    approval = await request_approval(
        session, tenant_id=auth.tenant_id, document_id=document_id, actor_id=auth.user_id,
    )
    return {"id": str(approval.id), "status": approval.status, "version": approval.version}


@router.post("/approvals/{approval_id}/decide")
async def post_approval_decision(
    approval_id: uuid.UUID,
    body: ApprovalDecisionIn,
    auth: AuthContext = Depends(require(CONTRACTS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    approval = await decide_approval(
        session, tenant_id=auth.tenant_id, approval_id=approval_id,
        decision=body.decision, approver_id=auth.user_id, notes=body.notes,
    )
    return {"id": str(approval.id), "status": approval.status}


@router.post("/{document_id}/signatures", status_code=201)
async def post_signature(
    document_id: uuid.UUID,
    body: SignatureIn,
    auth: AuthContext = Depends(require(CONTRACTS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    sig = await add_signature(
        session, tenant_id=auth.tenant_id, document_id=document_id,
        signer_name=body.signer_name, role=body.role,
        signer_person_id=body.signer_person_id, method=body.method,
    )
    return {"id": str(sig.id), "status": sig.status}


@router.post("/signatures/{signature_id}/sign")
async def do_sign(
    signature_id: uuid.UUID,
    auth: AuthContext = Depends(require(CONTRACTS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    sig = await sign(session, tenant_id=auth.tenant_id, signature_id=signature_id,
                     actor_id=auth.user_id)
    return {"id": str(sig.id), "status": sig.status, "signed_at": sig.signed_at.isoformat()}


@router.get("/{document_id}/signatures")
async def get_signatures(
    document_id: uuid.UUID,
    auth: AuthContext = Depends(require(CONTRACTS_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(DocumentSignature)
            .where(DocumentSignature.document_id == document_id,
                   DocumentSignature.tenant_id == auth.tenant_id)
        )
    ).scalars().all()
    return [
        {"id": str(s.id), "signer_name": s.signer_name, "role": s.role,
         "status": s.status, "signed_at": s.signed_at.isoformat() if s.signed_at else None}
        for s in rows
    ]
