"""Database layer.

PostgreSQL is the single source of truth (Architecture §1.1).

Serverless note: in production we connect through the Supabase pooler
(transaction mode). asyncpg prepared statements must be disabled for
pgbouncer transaction pooling — handled here once, globally.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

current_tenant_id: ContextVar[uuid.UUID | None] = ContextVar("current_tenant_id", default=None)


def _make_engine() -> AsyncEngine:
    kwargs: dict = {
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
    }
    if settings.app_env == "test":
        # Tests run across multiple event loops; pooled asyncpg connections
        # are loop-bound, so tests use NullPool.
        from sqlalchemy.pool import NullPool

        kwargs["poolclass"] = NullPool
        kwargs.pop("pool_size", None)
        kwargs.pop("max_overflow", None)
    if settings.database_url.startswith("postgresql+asyncpg"):
        # pgbouncer transaction mode compatibility (Supabase pooler)
        kwargs["connect_args"] = {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "server_settings": {"application_name": "revenue-os"},
        }
    return create_async_engine(settings.database_url, **kwargs)


engine: AsyncEngine = _make_engine()
session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=True)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Tenant GUC is applied inside the transaction so
    Row-Level Security policies (defense in depth, §82) can see it."""
    async with session_factory() as session:
        async with session.begin():
            tenant_id = current_tenant_id.get()
            if tenant_id is not None:
                # transaction-local GUC for RLS policies (defense in depth, §82);
                # SET LOCAL cannot take bind params — set_config() can.
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
            yield session
