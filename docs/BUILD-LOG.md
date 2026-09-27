# BUILD LOG — Real Estate Revenue OS

Delivery log per milestone with acceptance evidence.

## Completion Wave — full architecture parity (§19, §24, §36, §40-42, §57, §63, §98-100) ✅
- **Supabase deployed for real**: 81 tables + 72 tenant_isolation RLS policies + pgvector,
  applied via Management API (scripts/supabase_apply.py), alembic_version in lockstep with local
- **FTS (§24)**: generated tsvector + GIN on property_assets, websearch_to_tsquery + ts_rank in /assets?q=
- **Documents (§36)**: versions (new version invalidates approvals), approvals gate facts_verified (§85),
  signatures with double-sign protection + evidence
- **Reconciliation (§19)**: source precedence (developer > erp > partner > manual), freshness +
  confidence tie-break, auto-resolve only when unambiguous, manual review otherwise, full audit
- **Attribution (§41)**: first-touch immutable / last-touch updates, captured automatically on lead.created
- **Content engine (§42)**: AI generate → born DRAFT → review/approve → publish (publish blocked pre-approval)
- **Knowledge RAG (§57)**: semantic retrieval (pgvector cosine) injected into agent context per profile
- **AI Evaluation (§63)**: eval runner with tool-selection accuracy, response quality, escalation checks
- **Billing (§99)**: plans (trial/starter/pro/enterprise) with monthly quotas enforced on AI + messaging
- **Rate limiting (§98)**: DB-backed fixed-window counters, per-tenant keys, applied to AI + send
- **Feature flags (§100)**: enforced on gated agents (e.g. ai_reactivation)
- **Tests: 65 green** (12 new: FTS, documents, reconciliation, attribution, content gate,
  knowledge retrieval, eval runner, quota exhaustion + plan upgrade, rate window, AI quota 429)

## M1 — Platform Core ✅
- Multi-tenant tenancy (ContextVar + per-request GUC + RLS §82)
- Transactional outbox + exactly-once processing + retry/backoff/DLQ
- Postgres job queue: SKIP LOCKED + lease + crash requeue + dedup keys
- RBAC (8 system roles × ~40 permissions), idempotency framework, audit trail
- Idempotent tenant provisioning (tenant → org → branch → roles → owner → flags)
- **Tests: 23 invariant tests green**

## M2 — Engagement ✅
- Leads + state machine (§9) + Scoring Engine (one engine, versioned signals)
- Conversations + unified inbox + tasks
- Channel Gateway: adapter registry, Simulator + Meta WhatsApp Cloud API adapters
- Consent gate on ALL outbound (§5) — AI can never bypass
- Webhook pipeline: verify → persist raw → dedup → normalize → async
- **Tests: 30 cumulative — golden inbound flow, dedup, consent, lifecycle**

## M3 — Property & Supply ✅
- Developers/projects/buildings/assets (standalone OR unit §13)
- Inventory state machine (§17) with FOR UPDATE transactional holds
- **CONCURRENCY PROVEN: two parallel holds → exactly one wins (real Postgres)**
- Append-only price versioning + payment plans + mandates + listings with price snapshot
- **Tests: 38 cumulative**

## M4 — Search & Matching ✅
- property_search_documents read model (§105) + pgvector embeddings (Gemini/mock)
- Matching pipeline (§25): hard filters → candidates → semantic re-rank → business ranking
- Auto-match on requirements update (§112 hook)
- **Live E2E: 3 units ranked with reasons**

## M5 — Sales ✅
- Opportunities (§27) auto-convert leads via state machine path
- Viewings: timezone-aware (UTC+IANA), conflict detection, reschedule history
- Offers: versioned counters, immutable price snapshots, discount approval policy (§59)
- Reservations: transactional + idempotent + concurrency-safe (§35)
- **CONCURRENCY PROVEN: two parallel reservations → exactly one wins**
- **Tests: 44 cumulative — full golden flow §113**

## M6 — Transactions ✅
- Contracts from reservations (inventory → CONTRACTED §17)
- Deals + payment schedules + payments
- Commission engine: configurable rules → agency/sales_rep splits (§38)

## M7 — AI Platform ✅
- ONE Agent Runtime (review rule), 9 seeded profiles (§52-53)
- Tool Gateway (§58): 6 read + 6 write tools, classified safe/controlled/approval (§59)
- Guardrails (§60): mechanical check — price/availability claims without tool evidence
  → flagged + human escalation; flags persist in ledger
- Model Gateway (§61): Gemini (flash/pro + embeddings) + scripted Mock
- AI Action Ledger (§95): every execution — model, tokens, cost, tools, outcome
- NL analytics WITHOUT SQL generation (§73): deterministic plan → data service

## M8 — Automation ✅
- Rules engine (trigger/conditions/actions) on domain events
- Durable journeys: DB-backed steps + waits — survive crashes (§44)
- Deterministic SLA engine (§29): warn → breach → escalate → notifications
- **Tests: 53 cumulative — journey wait/resume, SLA breach + escalation**

## M9 — Intelligence ✅
- Operational snapshot + revenue funnel (§71) with conversion rates
- Arabic NL query planner (§73) — live E2E with structured plan output
- Usage metering (§99) for billing

## Delivery extras ✅
- Excel/CSV importer with row-level errors (properties + people)
- Jobs runner CLI + Vercel Cron tick endpoint (GET+POST, secret-protected)
- Alembic baseline (75 tables) + RLS migration (67 tenant-isolation policies)
- CI: lint + migration up/down/up + full suite on pgvector/pg17
- Frontend: Next.js 15 bilingual (ar RTL / en) — 9 pages, live E2E verified visually

## Live end-to-end verification (real running system)
WhatsApp message → identity → conversation + lead (auto-assign) → qualification →
matching (3 ranked units + reasons) → reply dispatched via adapter (`sent`) →
viewing (confirm/attend/complete) → offer 4,050,000 EGP → accepted →
reservation (idempotent) → contract (inventory CONTRACTED) → deal WON →
commissions (60,750 agency + 40,500 sales_rep) → funnel 100% conversion.

## V4 Architecture Wave — full implementation ✅

Per the V4 consolidated architecture (Capabilities/Decision Plane/Data Plane):

- **Unified Capability Gateway (PART 10)**: one grant model for ALL external actors
  (AI_AGENT, OUTBOUND_WEBHOOK, MARKETPLACE_APP, API_CLIENT) with scope + expiry + revocation.
  Threats closed: SSRF via webhooks (allowlist + DNS-resolve + private-IP/metadata block),
  marketplace over-scope (install consent → scoped expiring grants), custom-field collision
  (EAV side tables).
- **Unified Approval primitive (5.4)**: approval_requests consumed by every domain —
  role gates, expiry, payload snapshots.
- **Decision Plane (PART 5)**: policy → scoring → optimization → orchestrator as composable
  modules + Decision Records (5.3) — every major decision reproducible.
- **Commission splits DB invariant (1.29)**: SUM=100 trigger + remainder finalize function.
- **Inventory Ledger (3.4)**: append-only lifecycle history across all transitions.
- **Claims (4.4-4.5)**: provenance + truth_status lifecycle + bitemporal queries;
  EXTRACTED/INFERRED born UNVERIFIED — explicit verification gate before CURRENT.
- **Signal Engine (4.6)**: single definition, online/offline parity validator with drift alerts.
- **Staleness contracts (4.7)**: projection_registry + lag tracking + degraded mode.
- **Real-time gateway (PART 14)**: SSE tenant-scoped domain event stream.
- **NATS transport (4.3)**: optional tenant-scoped fan-out behind the durable pipeline.
- **AI trust filter (4.4)**: only VERIFIED claims reach the model context.
- **Tests: 97 green** (11 V4-specific). Tables: 95. RLS: 86 policies.

## Hardening Wave — external-audit remediation (27 findings → all addressed) ✅

P0 fixes:
- /auth/bootstrap tenant takeover: CLOSED — existing slug → 409, no auto-owner-attach
- AI approvals: boolean → REAL ApprovalRequest bound by args-hash (V4 5.4)
- AI effective permissions: agent ∩ caller (never union) — least privilege
- execute_tool now passes through the Capability Gateway (AI_AGENT grantee)
- Meta webhook routing: phone_number_id → exact decrypted account → fail-closed

P1 fixes:
- Price versioning: FOR UPDATE serialization + ONE-current-price partial unique index
- Offer versions: unique (tenant, opportunity, version) index
- Viewings: GiST EXCLUSION constraint — overlapping bookings IMPOSSIBLE in DB (TOCTOU-proof)
- Hold expiry: read truth == write truth (expired holds reconcile + ledger + event)
- Inventory ledger: records REAL from_state (no hardcoded AVAILABLE)
- Deal lifecycle: DealStateMachine — OPEN→CONTRACTED→WON (no direct skip)
- Idempotency race: IntegrityError → proper replay/conflict response
- Commission engine: scope-aware rule selection
- Budget/deadline: pre-check BEFORE each AI call (not post-hoc)
- Storage: path-traversal-proof filename sanitization
- requirements.txt: email-validator + nats-py added
- RLS completion: audit_log + claims + signals + projection_registry covered

Tests: 110 green (13 hardening-specific). Tables: 95. RLS: 90 policies.
