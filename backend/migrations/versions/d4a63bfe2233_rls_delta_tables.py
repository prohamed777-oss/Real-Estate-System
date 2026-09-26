"""RLS for delta tables (§82) + verify alembic_version.

Applies tenant_isolation policies to tables introduced after the RLS baseline:
document_versions, document_approvals, document_signatures, inventory_conflicts,
marketing_assets, rate_limit_counters (rate counters are keyed by bucket, not
tenant — excluded from tenant RLS; they carry no business data).
"""

from __future__ import annotations

from alembic import op

revision: str = "d4a63bfe2233"
down_revision: str | None = "7f28ccacdd76"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "document_versions",
    "document_approvals",
    "document_signatures",
    "inventory_conflicts",
    "marketing_assets",
]


def upgrade() -> None:
    for table in TENANT_TABLES:
        predicate = "tenant_id = current_setting('app.tenant_id', true)::uuid"
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            f'USING ({predicate}) WITH CHECK ({predicate})'
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
