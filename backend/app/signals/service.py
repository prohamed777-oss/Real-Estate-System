"""Signal Engine (V4 4.6) + Staleness contracts (4.7).

One definition per signal; the SAME transform is executed for the online path
(materialized signal_values) and the offline path (direct batch SQL over
canonical tables). The parity validator compares both paths on a sample —
divergence above threshold = Feature Drift alert.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.signals.models import ProjectionRegistry, SignalDefinition, SignalValue


# ---------- definitions ----------
async def define_signal(
    session, *, tenant_id: uuid.UUID | None, name: str, transform_sql: str,
    description: str | None = None, definition_version: str = "v1",
    online_ttl_seconds: int = 300,  # noqa: ANN001
) -> SignalDefinition:
    existing = (
        await session.execute(
            select(SignalDefinition).where(
                SignalDefinition.tenant_id == tenant_id,
                SignalDefinition.name == name,
                SignalDefinition.definition_version == definition_version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = SignalDefinition(
        tenant_id=tenant_id, name=name, transform_sql=transform_sql,
        description=description, definition_version=definition_version,
        online_ttl_seconds=online_ttl_seconds,
    )
    session.add(row)
    await session.flush()
    return row


from sqlalchemy import select  # noqa: E402


def _bind_transform(transform_sql: str, tenant_id: uuid.UUID, entity_type: str,
                    entity_id: uuid.UUID) -> str:
    return (transform_sql
            .replace(":tenant_id", f"'{tenant_id}'")
            .replace(":entity_type", f"'{entity_type}'")
            .replace(":entity_id", f"'{entity_id}'"))


async def compute_offline(session: AsyncSession, *, definition: SignalDefinition,
                          tenant_id: uuid.UUID, entity_type: str,
                          entity_id: uuid.UUID) -> float:
    """Offline/batch path: run the definition transform DIRECTLY on canonical tables."""
    if not definition.transform_sql:
        raise NotFound("Signal definition has no transform")
    sql = _bind_transform(definition.transform_sql, tenant_id, entity_type, entity_id)
    res = await session.execute(text(sql))
    val = res.scalar_one_or_none()
    return float(val) if val is not None else 0.0


async def refresh_online_signal(
    session: AsyncSession, *, tenant_id: uuid.UUID, entity_type: str,
    entity_id: uuid.UUID, definition: SignalDefinition,
) -> SignalValue:
    value = await compute_offline(
        session, definition=definition, tenant_id=tenant_id,
        entity_type=entity_type, entity_id=entity_id,
    )
    row = (
        await session.execute(
            select(SignalValue).where(
                SignalValue.tenant_id == tenant_id,
                SignalValue.entity_type == entity_type,
                SignalValue.entity_id == entity_id,
                SignalValue.name == definition.name,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = SignalValue(
            tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id,
            name=definition.name, definition_version=definition.definition_version,
        )
        session.add(row)
    row.value = value
    row.computed_at = datetime.now(UTC)
    row.expires_at = datetime.now(UTC) + timedelta(seconds=definition.online_ttl_seconds)
    row.freshness = "fresh"
    await session.flush()
    return row


async def read_online_signal(session: AsyncSession, *, tenant_id: uuid.UUID,
                             entity_type: str, entity_id: uuid.UUID,
                             name: str) -> dict[str, Any]:
    """Online read: fast materialized value with expiry check; expired → stale."""
    row = (
        await session.execute(
            select(SignalValue).where(
                SignalValue.tenant_id == tenant_id,
                SignalValue.entity_type == entity_type,
                SignalValue.entity_id == entity_id,
                SignalValue.name == name,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return {"name": name, "value": None, "freshness": "missing"}
    if row.expires_at and row.expires_at < datetime.now(UTC):
        row.freshness = "stale"
        await session.flush()
    return {"name": name, "value": float(row.value) if row.value is not None else None,
             "freshness": row.freshness, "computed_at": row.computed_at.isoformat()}


async def validate_parity(session: AsyncSession, *, tenant_id: uuid.UUID,
                          entity_type: str, entity_id: uuid.UUID,
                          name: str, threshold: float = 0.01) -> dict[str, Any]:
    """Parity Validator (1.32): online value vs fresh offline computation.
    Divergence > threshold = feature drift."""
    definition = (
        await session.execute(
            select(SignalDefinition).where(
                SignalDefinition.tenant_id == tenant_id, SignalDefinition.name == name,
            )
        )
    ).scalar_one_or_none()
    if definition is None:
        raise NotFound("Signal definition not found")
    offline_value = await compute_offline(
        session, definition=definition, tenant_id=tenant_id,
        entity_type=entity_type, entity_id=entity_id,
    )
    online = await read_online_signal(session, tenant_id=tenant_id,
                                       entity_type=entity_type, entity_id=entity_id, name=name)
    online_value = online.get("value") or 0.0
    drift = abs(offline_value - online_value)
    return {"name": name, "online": online_value, "offline": offline_value,
             "drift": round(drift, 6), "drifted": drift > threshold}


# ---------- Staleness contracts (V4 4.7) ----------
async def register_projection(session: AsyncSession, *, tenant_id: uuid.UUID,
                              projection_name: str, max_staleness_ms: int = 60000,
                              critical_path: bool = False, environment: str = "production",
                              projection_type: str = "read_model") -> ProjectionRegistry:
    row = (
        await session.execute(
            select(ProjectionRegistry).where(
                ProjectionRegistry.tenant_id == tenant_id,
                ProjectionRegistry.projection_name == projection_name,
                ProjectionRegistry.environment == environment,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ProjectionRegistry(
            tenant_id=tenant_id, projection_name=projection_name,
            environment=environment, max_staleness_ms=max_staleness_ms,
            critical_path=critical_path, projection_type=projection_type,
        )
        session.add(row)
    else:
        row.max_staleness_ms = max_staleness_ms
        row.critical_path = critical_path
    await session.flush()
    return row


async def check_projection_staleness(session: AsyncSession, *, tenant_id: uuid.UUID,
                                     projection_name: str) -> dict[str, Any]:
    """Contract check: current_lag_ms <= max_staleness_ms, else degraded mode."""
    row = (
        await session.execute(
            select(ProjectionRegistry).where(
                ProjectionRegistry.tenant_id == tenant_id,
                ProjectionRegistry.projection_name == projection_name,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Projection not registered")
    now = datetime.now(UTC)
    if row.last_event_processed_at is None:
        # a projection that has NEVER processed an event is stale by
        # definition, not silently healthy (V4 4.7)
        row.current_lag_ms = row.max_staleness_ms + 1
    else:
        row.current_lag_ms = int((now - row.last_event_processed_at).total_seconds() * 1000)
    within = row.current_lag_ms <= row.max_staleness_ms
    if not within and row.critical_path:
        # critical-path projections NEVER serve stale data (4.7 rule)
        row.degraded_mode = True
    elif within:
        row.degraded_mode = False
    await session.flush()
    return {"projection": row.projection_name, "lag_ms": row.current_lag_ms,
             "max_staleness_ms": row.max_staleness_ms, "within_contract": within,
             "degraded_mode": row.degraded_mode, "critical_path": row.critical_path}


async def mark_projection_processed(session: AsyncSession, *, tenant_id: uuid.UUID,
                                    projection_name: str) -> None:
    row = (
        await session.execute(
            select(ProjectionRegistry).where(
                ProjectionRegistry.tenant_id == tenant_id,
                ProjectionRegistry.projection_name == projection_name,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        row.last_event_processed_at = datetime.now(UTC)
        row.current_lag_ms = 0
        row.degraded_mode = False
        await session.flush()
