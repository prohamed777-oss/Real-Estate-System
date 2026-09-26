"""Idempotency framework (§1.7, §48).

Two levels:
1. API level: `Idempotency-Key` header on unsafe mutations. Same key + same
   request hash replays the stored response; different hash = conflict.
2. Event level: `processed_events` table ensures event handling is exactly-once
   (see app/events).

Storage model `idempotency_keys` lives in organizations.models (core tables).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import IdempotencyConflict


def payload_hash(body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyGuard:
    """Usage inside a route handler:

        guard = IdempotencyGuard(session, auth.tenant_id)
        replay = await guard.begin(key, body)
        if replay is not None:
            return replay
        ... perform mutation ...
        await guard.commit(response_dict)
    """

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self._key: str | None = None

    async def begin(self, key: str | None, body: Any) -> dict[str, Any] | None:
        if not key:
            return None
        self._key = key
        ph = payload_hash(body)
        from app.organizations.models import IdempotencyKey

        existing = (
            await self.session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.tenant_id == self.tenant_id,
                    IdempotencyKey.key == key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_hash != ph:
                raise IdempotencyConflict(
                    "Idempotency-Key was already used with a different payload",
                    details={"key": key},
                )
            if existing.response_snapshot is not None:
                return existing.response_snapshot
            # Same key, still in flight → treat as replay-wait; reject with conflict
            raise IdempotencyConflict("Request with this Idempotency-Key is still in flight")
        self.session.add(
            IdempotencyKey(
                tenant_id=self.tenant_id,
                key=key,
                request_hash=ph,
                status="in_flight",
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        await self.session.flush()
        return None

    async def commit(self, response: dict[str, Any]) -> None:
        if not self._key:
            return
        from app.organizations.models import IdempotencyKey

        row = (
            await self.session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.tenant_id == self.tenant_id,
                    IdempotencyKey.key == self._key,
                )
            )
        ).scalar_one()
        row.response_snapshot = response
        row.status = "completed"
        await self.session.flush()
