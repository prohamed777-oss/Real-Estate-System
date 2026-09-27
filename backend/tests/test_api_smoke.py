"""API smoke tests: health, dev bootstrap, auth/me, people CRUD, cron tick."""

from __future__ import annotations

from sqlalchemy import select

from app.organizations.models import Tenant


async def test_health(api):
    res = await api.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


async def test_dev_bootstrap_provisions_tenant(api, db):
    res = await api.post(
        "/api/v1/dev/bootstrap",
        json={
            "tenant_name": "Smoke Realty",
            "org_name": "Smoke",
            "owner_email": "smoke-owner@test.io",
            "owner_full_name": "Smoke Owner",
            "slug": "smoke",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "provisioned"
    tenant_row = (await db.execute(select(Tenant).where(Tenant.slug == "smoke"))).scalar_one()
    assert tenant_row is not None


async def test_me_and_people_flow(api, db, tenant):
    # dev login as owner
    res = await api.get("/api/v1/auth/me", headers={"X-Dev-Email": "owner@acme.test"})
    assert res.status_code == 200, res.text
    me = res.json()
    assert me["user"]["email"] == "owner@acme.test"
    assert me["role"] == "owner"
    assert "leads:read" in me["permissions"]

    headers = {"X-Dev-Email": "owner@acme.test"}

    # create person
    res = await api.post(
        "/api/v1/people",
        json={"full_name": "Ahmed Hassan", "email": "ahmed@example.com", "phone": "+201000000001"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    person = res.json()
    pid = person["id"]

    # list
    res = await api.get("/api/v1/people?q=ahmed", headers=headers)
    assert res.status_code == 200
    assert any(p["id"] == pid for p in res.json()["items"])

    # get detail with profile
    res = await api.get(f"/api/v1/people/{pid}", headers=headers)
    assert res.status_code == 200
    assert res.json()["profile"]["language"] == "ar"

    # consent
    res = await api.put(
        f"/api/v1/people/{pid}/consents",
        json={"channel": "whatsapp", "status": "opt_in", "consent_type": "marketing"},
        headers=headers,
    )
    assert res.status_code == 200

    # unauthenticated is rejected
    res = await api.get("/api/v1/people")
    assert res.status_code == 401


async def test_cron_tick_requires_secret(api):
    res = await api.post("/api/v1/internal/jobs/tick")
    assert res.status_code == 401
    res = await api.post("/api/v1/internal/jobs/tick", headers={"X-Cron-Secret": "test-cron-secret"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
