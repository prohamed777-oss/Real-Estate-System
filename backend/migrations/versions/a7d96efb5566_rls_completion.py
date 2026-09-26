"""RLS coverage completion: audit_log + claims/signal/projection tables created
after their respective RLS migrations. outbox_events + jobs are INTERNAL
transport tables deliberately excluded (workers process cross-tenant batches
as the postgres owner; documented in docs/decisions.md ADR-005)."""

from __future__ import annotations

from alembic import op

revision: str = "a7d96efb5566"
down_revision: str | None = "52ba6440e17d"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "audit_log",
    "claims",
    "signal_definitions",
    "signal_values",
    "projection_registry",
]

NULLABLE_TENANT_TABLES = ["signal_definitions"]


def upgrade() -> None:
    for table in TENANT_TABLES:
        if table in NULLABLE_TENANT_TABLES:
            predicate = (
                "tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)::uuid"
            )
        else:
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
