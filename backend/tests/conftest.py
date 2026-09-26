"""Shared test fixtures.

Environment is pinned BEFORE any app import. Tests run against a real local
PostgreSQL (revenue_os_test) because tenant isolation, SKIP LOCKED, and RLS
are Postgres-specific — faking them would prove nothing (review rule #2).

Service-layer convention: services flush, they never commit. The API layer
commits (via get_session). Tests that call services wrap in `async with db.begin()`.
"""

from __future__ import annotations

import os

os.environ["APP_ENV"] = "test"
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://@localhost:5432/revenue_os_test")
os.environ["AUTH_DEV_ENABLED"] = "true"
os.environ["CRON_SECRET"] = "test-cron-secret"
os.environ["AI_PROVIDER"] = "mock"

import pytest
from sqlalchemy import text

CORE_MODEL_MODULES = [
    "app.events.models",
    "app.organizations.models",
    "app.identity.models",
    "app.leads.models",
    "app.conversations.models",
    "app.channels.models",
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
]


@pytest.fixture(scope="session", autouse=True)
async def _schema():
    import importlib

    from app.core.db import Base, engine

    for module in CORE_MODEL_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name and exc.name != module and not module.startswith(exc.name):
                raise
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_db(_schema):
    yield
    from app.core.db import Base, engine

    tables = list(Base.metadata.tables)
    if tables:
        cols = ", ".join(f'"{t}"' for t in tables)
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {cols} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def db():
    from app.core.db import session_factory

    async with session_factory() as session:
        yield session


@pytest.fixture
async def tenant(db):
    from app.organizations.provisioning import provision_tenant

    async with db.begin():
        t = await provision_tenant(
            db,
            tenant_name="Acme Realty",
            org_name="Acme",
            owner_email="owner@acme.test",
            owner_full_name="Test Owner",
            slug="acme",
        )
    return t


@pytest.fixture
async def owner_ctx(tenant):
    """Auth context for the provisioned owner (bound to this async context)."""
    from sqlalchemy import select

    from app.core import tenancy
    from app.core.db import session_factory
    from app.core.permissions import ALL_PERMISSIONS
    from app.organizations.models import User

    async with session_factory() as s:
        user = (await s.execute(select(User).where(User.email == "owner@acme.test"))).scalar_one()
    ctx = tenancy.AuthContext(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role_key="owner",
        permissions=frozenset(ALL_PERMISSIONS),
        email=user.email,
    )
    tenancy.bind_auth(ctx)
    yield ctx
    tenancy.clear_auth()


@pytest.fixture
async def api():
    """HTTP client against the ASGI app (no network)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
