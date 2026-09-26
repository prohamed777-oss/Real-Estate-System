"""Document lifecycle services (§36, §85): versions, approvals, signatures.

Extracted facts are NOT truth until approved (§85) — the approval flow gates them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.events.outbox import emit
from app.finance.models import Document
from app.properties.models_ext import DocumentApproval, DocumentSignature, DocumentVersion


async def create_document(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: str, title: str,
    entity_type: str | None = None, entity_id: uuid.UUID | None = None,
    storage_path: str | None = None, created_by: str | None = None, actor_id=None,
) -> Document:
    owner_id = None
    try:
        owner_id = uuid.UUID(str(actor_id)) if actor_id else None
    except (ValueError, TypeError):
        owner_id = None
    doc = Document(
        tenant_id=tenant_id, kind=kind, title=title, entity_type=entity_type,
        entity_id=entity_id, storage_path=storage_path, status="draft",
        source="upload", owner_id=owner_id,
    )
    session.add(doc)
    await session.flush()
    # v1 snapshot
    session.add(DocumentVersion(
        tenant_id=tenant_id, document_id=doc.id, version=1,
        storage_path=storage_path, created_by=created_by,
    ))
    await audit(session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
                action="document.created", entity_type="document", entity_id=doc.id,
                after={"kind": kind, "title": title})
    return doc


async def add_version(
    session: AsyncSession, *, tenant_id: uuid.UUID, document_id: uuid.UUID,
    storage_path: str | None = None, change_note: str | None = None, created_by: str | None = None,
) -> DocumentVersion:
    doc = await session.get(Document, document_id)
    if doc is None or doc.tenant_id != tenant_id:
        raise NotFound("Document not found")
    if doc.status == "archived":
        raise Conflict("Cannot version an archived document")
    latest = (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version.desc())
            .limit(1)
        )
    ).scalar_one()
    version = DocumentVersion(
        tenant_id=tenant_id, document_id=document_id, version=latest.version + 1,
        storage_path=storage_path, change_note=change_note, created_by=created_by,
    )
    session.add(version)
    doc.version = latest.version + 1
    if storage_path:
        doc.storage_path = storage_path
    # new version invalidates prior approvals (they approved the old content)
    doc.status = "draft"
    doc.facts_verified = False
    await session.flush()
    await emit(
        session, event_name="document.version_added", tenant_id=tenant_id,
        aggregate_type="document", aggregate_id=document_id,
        payload={"document_id": str(document_id), "version": version.version},
    )
    return version


async def request_approval(
    session: AsyncSession, *, tenant_id: uuid.UUID, document_id: uuid.UUID,
    approver_id: uuid.UUID | None = None, actor_id=None,
) -> DocumentApproval:
    doc = await session.get(Document, document_id)
    if doc is None or doc.tenant_id != tenant_id:
        raise NotFound("Document not found")
    approval = DocumentApproval(
        tenant_id=tenant_id, document_id=document_id, version=doc.version,
        approver_id=approver_id, status="pending",
    )
    session.add(approval)
    await session.flush()
    return approval


async def decide_approval(
    session: AsyncSession, *, tenant_id: uuid.UUID, approval_id: uuid.UUID,
    decision: str, approver_id: uuid.UUID, notes: str | None = None,
) -> DocumentApproval:
    if decision not in ("approved", "rejected"):
        raise ValidationFailed("decision must be approved or rejected")
    approval = await session.get(DocumentApproval, approval_id)
    if approval is None or approval.tenant_id != tenant_id:
        raise NotFound("Approval not found")
    if approval.status != "pending":
        raise Conflict("Approval already decided")
    approval.status = decision
    approval.approver_id = approver_id
    approval.notes = notes
    approval.decided_at = datetime.now(UTC)
    doc = await session.get(Document, approval.document_id)
    if decision == "approved":
        doc.status = "approved"
        doc.facts_verified = True  # §85: extracted facts become usable post-approval
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=approver_id,
        action="document.approved" if decision == "approved" else "document.rejected",
        entity_type="document", entity_id=approval.document_id,
        after={"version": approval.version, "notes": notes},
    )
    return approval


async def add_signature(
    session: AsyncSession, *, tenant_id: uuid.UUID, document_id: uuid.UUID,
    signer_name: str, role: str | None = None, signer_person_id: uuid.UUID | None = None,
    signer_user_id: uuid.UUID | None = None, method: str = "manual",
) -> DocumentSignature:
    sig = DocumentSignature(
        tenant_id=tenant_id, document_id=document_id, signer_name=signer_name,
        signer_person_id=signer_person_id, signer_user_id=signer_user_id,
        role=role, method=method,
    )
    session.add(sig)
    await session.flush()
    return sig


async def sign(
    session: AsyncSession, *, tenant_id: uuid.UUID, signature_id: uuid.UUID,
    actor_id=None, evidence: dict[str, Any] | None = None,
) -> DocumentSignature:
    sig = await session.get(DocumentSignature, signature_id)
    if sig is None or sig.tenant_id != tenant_id:
        raise NotFound("Signature not found")
    if sig.status == "signed":
        raise Conflict("Already signed")
    sig.status = "signed"
    sig.signed_at = datetime.now(UTC)
    sig.evidence = evidence or {"signed_via": method_context(sig.method)}
    await audit(
        session, tenant_id=tenant_id, actor_type="user", actor_id=actor_id,
        action="document.signed", entity_type="document", entity_id=sig.document_id,
        after={"signer": sig.signer_name, "role": sig.role},
    )
    await emit(
        session, event_name="document.signed", tenant_id=tenant_id,
        aggregate_type="document", aggregate_id=sig.document_id,
        payload={"signature_id": str(sig.id), "signer": sig.signer_name},
    )
    return sig


def method_context(method: str) -> str:
    return {"manual": "wet-ink scanned", "digital": "e-signature", "otp": "otp-verified"}.get(method, method)
