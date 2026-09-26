"""Custom Fields service (V4 10.5): define once per tenant, set/get values."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.capability.models import CustomFieldDefinition, CustomFieldValue
from app.core.errors import Conflict, NotFound, ValidationFailed

FIELD_TYPES = {"text", "number", "date", "select", "multiselect", "boolean"}


async def define_field(
    session: AsyncSession, *, tenant_id: uuid.UUID, entity_type: str, field_key: str,
    field_type: str = "text", label: dict[str, str] | None = None,
    options: list | None = None, created_by: str | None = None,
) -> CustomFieldDefinition:
    if field_type not in FIELD_TYPES:
        raise ValidationFailed(f"field_type must be one of {sorted(FIELD_TYPES)}")
    if not field_key or not field_key.replace("_", "").isalnum():
        raise ValidationFailed("field_key must be alphanumeric/underscore")
    existing = (
        await session.execute(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.tenant_id == tenant_id,
                CustomFieldDefinition.entity_type == entity_type,
                CustomFieldDefinition.field_key == field_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("Custom field already defined for this entity type")
    row = CustomFieldDefinition(
        tenant_id=tenant_id, entity_type=entity_type, field_key=field_key,
        field_type=field_type, label=label or {}, options=options or [],
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def _get_definition(session: AsyncSession, *, tenant_id: uuid.UUID,
                          entity_type: str, field_key: str) -> CustomFieldDefinition:
    row = (
        await session.execute(
            select(CustomFieldDefinition).where(
                CustomFieldDefinition.tenant_id == tenant_id,
                CustomFieldDefinition.entity_type == entity_type,
                CustomFieldDefinition.field_key == field_key,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Custom field not defined")
    return row


def _validate_value(definition: CustomFieldDefinition, value: Any) -> Any:
    t = definition.field_type
    if value is None:
        return None
    if t == "number":
        return float(value)
    if t == "boolean":
        return bool(value)
    if t == "date":
        return str(value)
    if t == "select":
        if definition.options and value not in definition.options:
            raise ValidationFailed(f"{value!r} is not an allowed option")
        return str(value)
    if t == "multiselect":
        vals = list(value or [])
        if definition.options:
            bad = [v for v in vals if v not in definition.options]
            if bad:
                raise ValidationFailed(f"Options not allowed: {bad}")
        return vals
    return str(value)


async def set_value(
    session: AsyncSession, *, tenant_id: uuid.UUID, entity_type: str,
    entity_id: uuid.UUID, field_key: str, value: Any,
) -> CustomFieldValue:
    definition = await _get_definition(session, tenant_id=tenant_id,
                                        entity_type=entity_type, field_key=field_key)
    validated = _validate_value(definition, value)
    row = (
        await session.execute(
            select(CustomFieldValue).where(
                CustomFieldValue.tenant_id == tenant_id,
                CustomFieldValue.entity_type == entity_type,
                CustomFieldValue.entity_id == entity_id,
                CustomFieldValue.field_key == field_key,
            )
        )
    ).scalar_one_or_none()
    if validated is None:
        if row is not None:
            await session.delete(row)
            await session.flush()
        return row  # type: ignore[return-value]
    if row is None:
        row = CustomFieldValue(
            tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id,
            field_key=field_key, value={"v": validated},
        )
        session.add(row)
    else:
        row.value = {"v": validated}
    await session.flush()
    return row


async def get_values(session: AsyncSession, *, tenant_id: uuid.UUID,
                     entity_type: str, entity_id: uuid.UUID) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(CustomFieldValue).where(
                CustomFieldValue.tenant_id == tenant_id,
                CustomFieldValue.entity_type == entity_type,
                CustomFieldValue.entity_id == entity_id,
            )
        )
    ).scalars().all()
    return {r.field_key: r.value.get("v") for r in rows}
