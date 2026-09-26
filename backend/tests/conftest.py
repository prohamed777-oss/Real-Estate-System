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
    "app.events.history",
    "app.organizations.models",
    "app.identity.models",
    "app.leads.models",
    "app.conversations.models",
    "app.channels.models",
    "app.properties.models",
    "app.properties.models_ext",
    "app.decision.models",
    "app.capability.models",
    "app.listings.models",
    "app.matching.models",
    "app.sales.models",
    "app.finance.models",
    "app.marketing.models",
    "app.marketing.models_ext",
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
        importlib.import_module(module)
    async with engine.begin() as conn:
        # bulletproof reset: nuke everything including late-registered tables
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # migration-managed generated columns/indexes (§24) applied manually here
        await conn.execute(text("""
            ALTER TABLE property_assets ADD COLUMN IF NOT EXISTS search_vector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('simple',
                    coalesce(title, '') || ' ' ||
                    coalesce(location->>'city', '') || ' ' ||
                    coalesce(location->>'area', '') || ' ' ||
                    coalesce(location->>'compound', '')
                )
            ) STORED
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_assets_fts ON property_assets USING GIN (search_vector)"
        ))
        # V4 1.29 trigger (migration-managed) applied manually here
        await conn.execute(text("""
            CREATE OR REPLACE FUNCTION check_commission_split_total() RETURNS trigger AS $$
            DECLARE
                total NUMERIC;
            BEGIN
                SELECT COALESCE(SUM(share_percentage), 0) INTO total
                FROM commission_splits WHERE deal_id = NEW.deal_id;
                IF total > 100.00 THEN
                    RAISE EXCEPTION 'commission splits for deal % total % (must be exactly 100.00)',
                        NEW.deal_id, total;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """))
        await conn.execute(text("""
            CREATE TRIGGER trg_commission_split_total
            AFTER INSERT OR UPDATE ON commission_splits
            FOR EACH ROW EXECUTE FUNCTION check_commission_split_total();
        """))
        await conn.execute(text("""
            CREATE OR REPLACE FUNCTION finalize_commission_splits(
                p_deal_id UUID, p_agency_role TEXT DEFAULT 'agency'
            ) RETURNS void AS $$
            DECLARE
                total NUMERIC;
            BEGIN
                SELECT COALESCE(SUM(share_percentage), 0) INTO total
                FROM commission_splits WHERE deal_id = p_deal_id;
                IF total <> 100.00 THEN
                    UPDATE commission_splits
                    SET share_percentage = share_percentage + (100.00 - total)
                    WHERE deal_id = p_deal_id AND party_role = p_agency_role;
                END IF;
            END;
            $$ LANGUAGE plpgsql;
        """))
    # Seed global agent profiles (normally done by app lifespan)
    from app.core.db import session_factory as sf

    async with sf() as s:
        async with s.begin():
            from app.ai.agents import seed_default_profiles

            await seed_default_profiles(s)
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
    # Re-seed global agent profiles (truncation wiped them)
    from app.core.db import session_factory as sf

    async with sf() as s:
        async with s.begin():
            from app.ai.agents import seed_default_profiles

            await seed_default_profiles(s)


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
