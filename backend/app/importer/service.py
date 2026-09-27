"""Excel/CSV import (properties & people) with flexible column mapping."""

from __future__ import annotations

import io
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.identity.models import Person
from app.importer.models import ImportError_, ImportJob
from app.properties.service import create_asset, create_project

PROPERTY_FIELDS = {
    "title", "property_type", "asset_type", "bedrooms", "bathrooms", "area_value",
    "finishing", "floor", "view", "delivery_status", "city", "area", "price",
    "currency", "project_name",
}
PEOPLE_FIELDS = {"full_name", "email", "phone", "type", "tags"}


def parse_workbook(content: bytes, filename: str) -> list[dict[str, Any]]:
    """Parse xlsx/csv into a list of row dicts with normalized headers."""
    lower = filename.lower()
    rows: list[dict[str, Any]] = []
    if lower.endswith((".xlsx", ".xlsm", ".xls")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        header: list[str] | None = None
        for row in ws.iter_rows(values_only=True):
            if header is None:
                header = [str(c or "").strip().lower() for c in row]
                continue
            if not any(c is not None and str(c).strip() for c in row):
                continue
            rows.append({header[i]: row[i] for i in range(min(len(header), len(row)))})
    else:
        import csv as csv_mod

        text = content.decode("utf-8-sig", errors="replace")
        reader = csv_mod.DictReader(io.StringIO(text))
        for raw in reader:
            cleaned = { (k or "").strip().lower(): v for k, v in raw.items() }
            if any(str(v or "").strip() for v in cleaned.values()):
                rows.append(cleaned)
    return rows


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        cleaned = str(value).replace(",", "").strip()
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


async def run_import(
    session: AsyncSession, *, tenant_id: uuid.UUID, kind: str, filename: str,
    rows: list[dict[str, Any]], created_by: str | None = None,
) -> ImportJob:
    if kind not in ("properties", "people"):
        raise ValidationFailed("kind must be properties or people")
    job = ImportJob(
        tenant_id=tenant_id, kind=kind, filename=filename,
        status="running", total_rows=len(rows), created_by=created_by,
        mapping=rows[0] if rows else {},
    )
    session.add(job)
    await session.flush()

    errors = 0
    samples: list[dict] = []
    project_cache: dict[str, uuid.UUID] = {}

    for row_no, row in enumerate(rows, start=2):
        try:
            if kind == "properties":
                title = (row.get("title") or row.get("العنوان") or "").strip()
                if not title:
                    raise ValueError("title required")
                project_name = (row.get("project_name") or row.get("المشروع") or "").strip()
                project_id = None
                if project_name:
                    if project_name in project_cache:
                        project_id = project_cache[project_name]
                    else:
                        project = await create_project(
                            session, tenant_id=tenant_id, name=project_name,
                            location={"city": row.get("city") or "", "area": row.get("area") or ""},
                        )
                        project_cache[project_name] = project.id
                        project_id = project.id
                asset = await create_asset(
                    session, tenant_id=tenant_id, title=title,
                    property_type=(row.get("property_type") or "apartment").strip(),
                    asset_type="unit" if project_id else "standalone",
                    project_id=project_id,
                    bedrooms=int(_to_decimal(row.get("bedrooms")) or 0) or None,
                    bathrooms=int(_to_decimal(row.get("bathrooms")) or 0) or None,
                    area_value=_to_decimal(row.get("area_value") or row.get("المساحة")),
                    finishing=row.get("finishing"),
                    delivery_status=row.get("delivery_status"),
                    location={"city": row.get("city") or "", "area": row.get("area") or ""},
                    source_type="internal",
                )
                price = _to_decimal(row.get("price") or row.get("السعر"))
                if price:
                    from app.properties.service import set_price

                    await set_price(
                        session, tenant_id=tenant_id, asset_id=asset.id, amount=price,
                        currency=(row.get("currency") or "EGP").upper()[:3],
                        source="import",
                    )
            else:  # people
                full_name = (row.get("full_name") or row.get("الاسم") or "").strip()
                phone = (row.get("phone") or row.get("الهاتف") or "").strip() or None
                email = (row.get("email") or "").strip().lower() or None
                if not full_name and not phone and not email:
                    raise ValueError("name or phone required")
                existing = None
                if phone:
                    existing = (
                        await session.execute(
                            select(Person).where(
                                Person.tenant_id == tenant_id, Person.phone == phone
                            )
                        )
                    ).scalar_one_or_none()
                if existing is None:
                    session.add(Person(
                        tenant_id=tenant_id, type=row.get("type") or "customer",
                        full_name=full_name, phone=phone, email=email,
                        source="import",
                    ))
        except Exception as exc:  # noqa: BLE001 — per-row errors must not kill the import
            errors += 1
            session.add(ImportError_(
                job_id=job.id, tenant_id=tenant_id, row_no=row_no,
                error=f"{type(exc).__name__}: {exc}"[:500], raw={k: str(v) for k, v in row.items()},
            ))
            if len(samples) < 10:
                samples.append({"row": row_no, "error": str(exc)[:200]})
    await session.flush()

    job.ok_rows = job.total_rows - errors
    job.error_rows = errors
    job.errors_sample = samples
    job.status = "completed" if errors == 0 else ("partial" if job.ok_rows else "failed")
    job.finished_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
    await session.flush()
    return job
