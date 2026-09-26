# Real Estate Revenue OS

نظام تشغيل إيرادات العقارات — multi-tenant, AI-native, event-driven.
**The AI is not the system. The domain system is the system. AI is the intelligence layer that uses the system safely.**

## Architecture (frozen per docs/architecture.md + review rules)

```
Next.js (ar RTL/en, Vercel) ──► FastAPI (Vercel serverless / uvicorn)
                                   │
                      Domain Services (modular monolith)
                      Commands ≠ Queries · State Machines · Money VO
                                   │
              PostgreSQL (Supabase) ── source of truth, RLS defense-in-depth
                                   │
              Transactional Outbox ──► Job Queue (SKIP LOCKED + lease + DLQ)
                                   │
        AI Platform (ONE runtime, 9 profiles, Tool Gateway, guardrails, ledger)
        Channel Gateway (Simulator + Meta WhatsApp adapters)
```

### Hard rules implemented as code + permanent tests
| Rule | Where |
|---|---|
| DB commit → event cannot silently disappear | `app/events/outbox.py` + `tests/test_outbox_queue.py` |
| One unit can never have two active reservations | `app/properties/service.py::mark_reserved` (FOR UPDATE) + concurrency tests |
| AI proposes; domain decides — no SQL/availability/price from LLM | `app/ai/tools.py`, `app/ai/guardrails.py` |
| Tenant isolation on every query (app layer + RLS §82) | `app/core/tenancy.py`, `migrations/versions/*rls*` |
| Prices are append-only versions; offers keep immutable snapshots | `app/properties/service.py::set_price`, `app/sales/models.py::Offer` |
| Retry ≠ duplicate business action | idempotency keys + `processed_events` + unique reservation keys |
| Outbound respects consent | `app/channels/service.py::send_outbound_message` |
| Timezone-aware scheduling (UTC + IANA) | `app/sales/service.py::create_viewing` |
| Money = Numeric(18,4) + ISO currency, never floats | `app/core/money.py` |

## Stack
- **Backend**: FastAPI · SQLAlchemy 2 async · asyncpg · Alembic · Pydantic v2 · Python 3.12
- **Database**: Supabase Postgres (pooler, transaction mode) + pgvector + FTS
- **Jobs**: Postgres SKIP LOCKED queue; Vercel Cron tick (`/internal/jobs/tick`) or local runner
- **AI**: Gemini (gemini-2.5-flash / embeddings) + scripted Mock for tests; provider-agnostic gateway
- **Frontend**: Next.js 15 · Tailwind v4 · bilingual ar/en with RTL-first layout

## Run locally

```bash
# 0) Postgres 17 + pgvector running locally, DBs created
brew services start postgresql@17
createdb revenue_os && createdb revenue_os_test

# 1) Backend
cd backend
uv venv --python 3.12 && uv pip install -e ".[dev]"
alembic upgrade head                          # apply schema + RLS
uvicorn app.main:app --port 8000 &            # API + docs at /api/docs
python -m app.jobs.runner &                   # background workers

# 2) Provision a tenant (dev mode)
curl -X POST localhost:8000/api/v1/dev/bootstrap -H 'Content-Type: application/json' \
  -d '{"tenant_name":"My Realty","owner_email":"owner@my.test","slug":"my-realty"}'

# 3) Frontend
cd frontend && pnpm install && pnpm dev       # http://localhost:3000
# login with owner@my.test (dev mode, no password)
```

## Golden flow (fully covered by tests)
WhatsApp message → identity resolution → conversation + lead → qualification →
matching (hard filters + semantic) → viewing (conflict-safe) → offer (price
snapshot, discount approval) → reservation (transactional, idempotent) →
contract → deal → commissions → revenue analytics → journeys/SLA throughout.

## Integrations (wired, awaiting credentials)
| Integration | Status | Wire-up |
|---|---|---|
| Meta WhatsApp Cloud API | adapter complete | Settings → قنوات التواصل → paste token + phone_number_id |
| Supabase Auth | code complete | set SUPABASE_URL/JWT_SECRET; frontend uses @supabase/ssr |
| Vercel deploy | configs ready | backend: root deploy (vercel.json) · frontend: root=frontend |
| Voice AI | stubbed (§115 defers voice) | voice adapter interface ready in AI platform |

## Tests
```bash
cd backend && pytest tests/ -q      # 53 tests: invariants, concurrency, golden flow, AI, automation
```

## Docs
- `docs/architecture.md` — the full 120-section architecture (frozen)
- `docs/BUILD-LOG.md` — milestone-by-milestone build log with acceptance status
- `docs/decisions.md` — key engineering decisions & deviations rationale
