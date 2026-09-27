"""Importer models (§99-adjacent): Excel/CSV import jobs with row-level errors."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamped, UUIDPk


class ImportJob(Base, UUIDPk, Timestamped):
    __tablename__ = "import_jobs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # properties | people | leads
    filename: Mapped[str | None] = mapped_column(String(300))
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # column → field
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|validating|running|completed|failed|partial
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    ok_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    errors_sample: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportError_(Base, UUIDPk):
    __tablename__ = "import_errors"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_jobs.id"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    row_no: Mapped[int] = mapped_column(Integer)
    field: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str] = mapped_column(String(500))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
