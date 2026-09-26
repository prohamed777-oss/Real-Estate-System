"""Full-Text Search (§24): generated tsvector + GIN index on property_assets.

Structured filters and semantic retrieval stay as-is; FTS adds ranked keyword
search (Arabic-friendly via 'simple' config) — Search Index ≠ source of truth.

NOTE: also creates rate_limit_counters (§98) — table needed by core ratelimit.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c3f52aeda112"
down_revision: str | None = "b2e41fcfda01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # rate_limit_counters (§98)
    op.create_table(
        "rate_limit_counters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("bucket_key", sa.String(200), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("bucket_key", "window_start", name="uq_rate_bucket"),
    )
    op.create_index("ix_rate_window", "rate_limit_counters", ["window_start"])

    # FTS: generated tsvector over title + location text (§24, §105)
    op.execute("""
        ALTER TABLE property_assets ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            to_tsvector('simple',
                coalesce(title, '') || ' ' ||
                coalesce(location->>'city', '') || ' ' ||
                coalesce(location->>'area', '') || ' ' ||
                coalesce(location->>'compound', '')
            )
        ) STORED
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_assets_fts ON property_assets
        USING GIN (search_vector)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_assets_fts")
    op.execute("ALTER TABLE property_assets DROP COLUMN IF EXISTS search_vector")
    op.drop_table("rate_limit_counters")
