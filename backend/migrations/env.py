"""Alembic async environment."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.core.db import Base

# Import ALL model modules so autogenerate sees the full metadata.
# Tolerant during incremental development; final delivery requires zero warnings.
import importlib
import logging

log = logging.getLogger("alembic.env")
for _mod in [
    "app.events.models",
    "app.organizations.models",
    "app.identity.models",
    "app.leads.models",
    "app.conversations.models",
    "app.properties.models",
    "app.listings.models",
    "app.matching.models",
    "app.sales.models",
    "app.finance.models",
    "app.marketing.models",
    "app.automation.models",
    "app.ai.models",
    "app.analytics.models",
    "app.importer.models",
]:
    try:
        importlib.import_module(_mod)
    except ModuleNotFoundError as exc:
        if exc.name and exc.name != _mod and not _mod.startswith(exc.name):
            raise
        log.warning("model module not present yet: %s", _mod)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
