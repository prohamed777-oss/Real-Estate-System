"""Hardening wave: DB-enforced concurrency guarantees (external audit P1s).

- price_versions: ONE current price per asset (partial unique index)
- offers: unique (tenant, opportunity, version) — negotiation history integrity
- viewings: GiST EXCLUSION constraint on the salesperson schedule —
  overlapping bookings become impossible AT THE DATABASE (TOCTOU-proof)
"""

from __future__ import annotations

from alembic import op

revision: str = "b8e07afg6677"
down_revision: str | None = "a7d96efb5566"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    # one CURRENT price per asset — concurrent set_price cannot double-close
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_price_one_current
        ON price_versions (tenant_id, asset_id) WHERE valid_to IS NULL;
    """)
    # negotiation history integrity — one version per opportunity
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_offer_version
        ON offers (tenant_id, opportunity_id, version);
    """)
    # viewing overlap prevention AT THE DATABASE (status-guarded exclusion):
    # two ACTIVE viewings for the same salesperson cannot overlap in time.
    # make_interval is STABLE → not indexable; an IMMUTABLE SQL helper is
    op.execute("""
        CREATE OR REPLACE FUNCTION viewing_end(t timestamptz, mins int) RETURNS timestamptz
        AS $$ SELECT t + (mins * interval '1 minute') $$ LANGUAGE sql IMMUTABLE PARALLEL SAFE;
    """)
    op.execute("""
        ALTER TABLE viewings ADD CONSTRAINT ex_viewing_no_overlap
        EXCLUDE USING gist (
            tenant_id WITH =,
            salesperson_id WITH =,
            tstzrange(scheduled_at, viewing_end(scheduled_at, duration_minutes)) WITH &&
        )
        WHERE (status IN ('REQUESTED', 'CONFIRMED') AND salesperson_id IS NOT NULL)
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE viewings DROP CONSTRAINT IF EXISTS ex_viewing_no_overlap")
    op.execute("DROP FUNCTION IF EXISTS viewing_end(timestamptz, int)")
    op.execute("DROP INDEX IF EXISTS uq_offer_version")
    op.execute("DROP INDEX IF EXISTS uq_price_one_current")
