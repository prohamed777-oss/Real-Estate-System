"""Cursor (keyset) pagination for list endpoints.

Opaque cursor = base64(JSON({"sort": ..., "id": ...})). Stable, deep-linkable,
and safe for large tables (§90-91).
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class CursorPage(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False


def encode_cursor(*, created_at: datetime, id_: uuid.UUID) -> str:
    raw = json.dumps({"ts": created_at.isoformat(), "id": str(id_)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(raw["ts"]), uuid.UUID(raw["id"])
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid cursor") from exc


def cursor_created_at(cursor: str | None) -> datetime:
    if cursor is None:
        return datetime.now(UTC)
    return decode_cursor(cursor)[0]
