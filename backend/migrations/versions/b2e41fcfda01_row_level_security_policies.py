"""Row-Level Security policies (§82) — defense in depth.

Strategy (per §82: RLS + application authorization + tenant checks):
- RLS is ENABLED on every business table carrying tenant_id.
- Policies scope reads/writes to current_setting('app.tenant_id', true).
- We do NOT force RLS on the table owner: backend workers process cross-tenant
  batches legitimately, and the application layer is the primary wall. Any
  future non-owner role (reporting, support tooling) is fully isolated by RLS.
- Workers set app.tenant_id per unit of work anyway (dispatch/run loops), so a
  FORCE switch is a one-line change when justified.
"""

from __future__ import annotations

from alembic import op

revision: str = "b2e41fcfda01"
down_revision: str | None = "566e2816c9a7"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "organizations", "branches", "teams", "users", "memberships", "roles",
    "feature_flags", "idempotency_keys",
    "people", "identities", "communication_consents", "customer_profiles",
    "leads", "lead_requirements",
    "conversations", "messages", "conversation_assignments", "tasks",
    "channel_accounts", "webhook_events",
    "developers", "projects", "buildings", "property_assets", "supply_sources",
    "mandates", "unit_inventory", "inventory_holds", "price_versions", "payment_plans",
    "listings", "listing_channels",
    "property_search_documents", "lead_embeddings", "matching_runs",
    "opportunities", "viewings", "viewing_events", "offers", "negotiation_entries",
    "reservations",
    "documents", "contracts", "deals", "payments", "payment_schedules",
    "commission_rules", "commissions",
    "campaigns", "lead_sources", "attributions",
    "automation_rules", "journeys", "journey_instances", "journey_step_logs",
    "sla_policies", "sla_trackers", "notifications",
    "agent_profiles", "ai_executions", "knowledge_docs", "knowledge_chunks", "eval_runs",
    "usage_meters", "subscriptions",
    "import_jobs", "import_errors",
]

NULLABLE_TENANT_TABLES = {"agent_profiles"}


def upgrade() -> None:
    for table in TENANT_TABLES:
        allow_null = table in NULLABLE_TENANT_TABLES
        predicate = (
            "tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true)::uuid"
            if allow_null
            else "tenant_id = current_setting('app.tenant_id', true)::uuid"
        )
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
