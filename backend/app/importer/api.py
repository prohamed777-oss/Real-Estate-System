"""Importer API: upload Excel/CSV, run import, check job status."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.db import get_session
from app.core.errors import NotFound, ValidationFailed
from app.core.permissions import IMPORT_RUN, require
from app.core.tenancy import AuthContext
from app.importer.models import ImportJob
from app.importer.service import parse_workbook, run_import

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("", status_code=201)
async def upload_import(
    kind: str = Form(...),
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require(IMPORT_RUN)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if kind not in ("properties", "people"):
        raise ValidationFailed("kind must be properties or people")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise ValidationFailed("File too large (max 10MB)")
    try:
        rows = parse_workbook(content, file.filename or "upload.csv")
    except Exception as exc:  # noqa: BLE001
        raise ValidationFailed(f"Cannot parse file: {exc}")
    if not rows:
        raise ValidationFailed("No data rows found")
    job = await run_import(
        session, tenant_id=auth.tenant_id, kind=kind, filename=file.filename or "upload",
        rows=rows, created_by=str(auth.user_id),
    )
    await audit(
        session, tenant_id=auth.tenant_id, actor_type="user", actor_id=auth.user_id,
        action="import.run", entity_type="import_job", entity_id=job.id,
        after={"kind": kind, "rows": job.total_rows, "ok": job.ok_rows, "errors": job.error_rows},
    )
    return {
        "job_id": str(job.id), "status": job.status, "total_rows": job.total_rows,
        "ok_rows": job.ok_rows, "error_rows": job.error_rows,
        "errors_sample": job.errors_sample,
    }


@router.get("/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    auth: AuthContext = Depends(require(IMPORT_RUN)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    job = (
        await session.execute(
            select(ImportJob).where(ImportJob.id == job_id, ImportJob.tenant_id == auth.tenant_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFound("Import job not found")
    return {
        "id": str(job.id), "kind": job.kind, "status": job.status,
        "total_rows": job.total_rows, "ok_rows": job.ok_rows, "error_rows": job.error_rows,
        "errors_sample": job.errors_sample,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.get("")
async def list_jobs(
    auth: AuthContext = Depends(require(IMPORT_RUN)),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(ImportJob)
            .where(ImportJob.tenant_id == auth.tenant_id)
            .order_by(ImportJob.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return [
        {"id": str(j.id), "kind": j.kind, "status": j.status, "total_rows": j.total_rows,
         "ok_rows": j.ok_rows, "error_rows": j.error_rows}
        for j in rows
    ]
