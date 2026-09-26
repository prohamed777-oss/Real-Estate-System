"""RLS for V4 tables (§82 discipline extended to the new planes)."""

from __future__ import annotations

from alembic import op

revision: str = "f6c85dga4455"
down_revision: str | None = "e5b74caf3344"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "capability_grants",
    "webhook_destinations",
    "custom_field_definitions",
    "custom_field_values",
    "approval_requests",
    "decisions",
    "commission_splits",
    "inventory_ledger",
]

NULLABLE_TENANT_TABLES = ["domain_events"]  # system events may have NULL tenant


def upgrade() -> None:
    for table in TENANT_TABLES:
        predicate = "tenant_id = current_setting('app.tenant_id', true)::uuid"
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            f'USING ({predicate}) WITH CHECK ({predicate})'
        )
    for table in NULLABLE_TENANT_TABLES:
        predicate = "tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)::uuid"
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            f'USING ({predicate}) WITH CHECK ({predicate})'
        )


def downgrade() -> None:
    for table in TENANT_TABLES + NULLABLE_TENANT_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
