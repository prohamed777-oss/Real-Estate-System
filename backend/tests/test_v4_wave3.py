"""V4-3 tests: capability/decision/claims APIs, NATS transport, AI claims trust filter."""

from __future__ import annotations

import uuid

from app.capability.gateway import (
    derive_idempotency_key,
)
from app.events.nats_transport import subject_for
from app.identity.models import Person


async def test_nats_subject_mapping():
    tid = uuid.uuid4()
    assert subject_for(tid, "unit.reserved") == f"tenant.{tid}.unit.reserved"
    assert subject_for(None, "system.tick") == "tenant.system.system.tick"


async def test_capability_and_decision_api_flow(api, db, tenant, owner_ctx):
    headers = {"X-Dev-Email": "owner@acme.test"}

    # issue a grant via API
    res = await api.post(
        "/api/v1/capability/grants",
        json={"grantee_type": "API_CLIENT", "grantee_id": "partner-portal",
              "resource_scope": "leads", "action_scope": "read", "ttl_days": 7},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    # visible in list
    res = await api.get("/api/v1/capability/grants?grantee_id=partner-portal", headers=headers)
    assert any(g["grantee_id"] == "partner-portal" for g in res.json())
    # decision plane via API
    res = await api.post(
        "/api/v1/capability/decide",
        json={"action": "ASSIGN_LEAD", "subject": {"lead_id": "api-lead"},
              "candidates": [
                  {"entity_id": "rep1", "signals": {"workload": 1, "lead_value": 3000000,
                                                     "expertise_fit": 0.8}},
                  {"entity_id": "rep2", "signals": {"workload": 5, "lead_value": 500000,
                                                     "expertise_fit": 0.3}},
              ]},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["selected"] == {"entity_id": "rep1"}
    assert body["policy_evaluation"]["allow"] is True


async def test_custom_fields_api_flow(api, db, tenant, owner_ctx):
    headers = {"X-Dev-Email": "owner@acme.test"}
    res = await api.post(
        "/api/v1/capability/custom-fields/define",
        json={"entity_type": "property_asset", "field_key": "roof_age",
              "field_type": "number", "label": {"ar": "عمر السطح"}},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    res = await api.post(
        "/api/v1/capability/custom-fields/set",
        json={"entity_type": "property_asset", "entity_id": str(uuid.uuid4()),
              "field_key": "roof_age", "value": 7},
        headers=headers,
    )
    assert res.status_code == 200


async def test_ai_context_includes_only_verified_claims(db, tenant, owner_ctx):
    """V4 4.4 trust filter: UNVERIFIED claims never reach the model context."""

    from app.ai.runtime import get_verified_claims
    from app.claims.service import assert_claim, verify_claim

    async with db.begin():
        person = Person(tenant_id=tenant.id, full_name="عميل كليمز", phone="+201333333333")
        db.add(person)
        await db.flush()
        pid = person.id
        await assert_claim(
            db, tenant_id=tenant.id, entity_type="lead", entity_id=uuid.uuid4(),
            field="max_budget", value="9,000,000", source="whatsapp",
            assertion_type="EXTRACTED",  # untrusted → UNVERIFIED
        )
        verified = await assert_claim(
            db, tenant_id=tenant.id, entity_type="person", entity_id=pid,
            field="preferred_area", value="مدينة نصر", source="call",
            assertion_type="OBSERVED",
        )
        await verify_claim(db, tenant_id=tenant.id, claim_id=verified.id,
                           verified_by="agent")
    claims = await get_verified_claims(db, tenant_id=tenant.id, person_id=pid)
    fields = [c["field"] for c in claims]
    assert "preferred_area" in fields
    assert "max_budget" not in fields, "UNVERIFIED claims must not reach the model"


def test_idempotency_derivation_documented():
    # V4 1.28 — keys derived in the gateway, never invented by callers
    a = derive_idempotency_key("exec", "action", {"x": 1})
    b = derive_idempotency_key("exec", "action", {"x": 1})
    c = derive_idempotency_key("exec", "action", {"x": 2})
    assert a == b and a != c
