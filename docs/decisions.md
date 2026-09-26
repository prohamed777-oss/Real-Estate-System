# Engineering Decisions (ADR summary)

## ADR-001: Modular monolith on FastAPI + Supabase Postgres
Per architecture §117. Domain modules keep API ≠ business ≠ DB separation (§87)
with commands/queries separation inside application services.

## ADR-002: Postgres queue instead of Redis in V1 (review-approved)
`jobs` table with FOR UPDATE SKIP LOCKED, lease_until + crash requeue,
exponential backoff, max_attempts → dead-letter. Domains call `enqueue()` only —
swap to Redis/broker later without touching domain code.

## ADR-003: One Agent Runtime, profiles not agents (review-approved)
`ai/runtime.py::execute_agent` is the single loop with hard limits (§55).
9 default profiles seeded globally (reception, qualification, matching,
scheduling, followup, reactivation, sales_copilot, manager_copilot, analytics).
Each profile = system prompt + tool scopes + guardrails + model profile + limits,
versioned, with ledger rows per execution (§95).

## ADR-004: Scoring engine with composable signals (review-approved)
One engine (`leads/scoring.py`), versioned outputs + signals snapshot stored on
the lead (score_version, score_signals, score_calculated_at).

## ADR-005: RLS enabled WITHOUT FORCE on owner role
App layer is the primary wall (tenant GUC set per request AND per outbox/job
unit of work). RLS isolates any non-owner access path. FORCE is a one-line
migration change when workers move off the owner role. Matches §82's
"RLS + application authorization + tenant checks — not RLS alone".

## ADR-006: Vercel serverless compatibility
API deployed as serverless functions (backend/api/index.py); connection string
expects the Supabase pooler in transaction mode (prepared statements disabled in
db.py); workers driven by Vercel Cron GET /internal/jobs/tick with the same
_tick() code path as the local runner.

## ADR-007: Guardrails are mechanical, not prompt tricks
Post-output check (guardrails.py) compares the message against the tool-call
trace: price/availability claims with no corroborating tool call are flagged and
escalate to a human. Flags persist in the AI ledger.

## ADR-008: i18n from day one
Listings store {ar, en} JSONB; UI is bilingual RTL-first; AI uses the person's
locale (person.locale / conversation language) rather than live translation.
