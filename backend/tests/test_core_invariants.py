"""Invariants: idempotency replay/conflict + RBAC + state machines + Money."""

from __future__ import annotations

import pytest

from app.core.errors import Conflict, IdempotencyConflict, PermissionDenied
from app.core.idempotency import IdempotencyGuard, payload_hash
from app.core.money import Money
from app.core.permissions import SETTINGS_WRITE, SYSTEM_ROLES
from app.core.statemachine import StateMachine


class DemoMachine(StateMachine):
    initial = "NEW"
    transitions = {
        "NEW": {"start": "RUNNING", "cancel": "CANCELLED"},
        "RUNNING": {"finish": "DONE", "cancel": "CANCELLED"},
    }
    terminal = {"DONE", "CANCELLED"}


# ---------- idempotency ----------
async def test_idempotent_replay_returns_same_response(db, tenant, owner_ctx):
    async with db.begin():
        guard = IdempotencyGuard(db, tenant.id)
        body = {"amount": 100}
        assert await guard.begin("key-1", body) is None
        await guard.commit({"result": "first"})

    async with db.begin():
        guard2 = IdempotencyGuard(db, tenant.id)
        replay = await guard2.begin("key-1", body)
    assert replay == {"result": "first"}


async def test_idempotency_conflicts_on_different_payload(db, tenant, owner_ctx):
    async with db.begin():
        guard = IdempotencyGuard(db, tenant.id)
        await guard.begin("key-2", {"a": 1})
        await guard.commit({"ok": True})
    async with db.begin():
        guard2 = IdempotencyGuard(db, tenant.id)
        with pytest.raises(IdempotencyConflict):
            await guard2.begin("key-2", {"a": 2})


def test_payload_hash_is_stable():
    assert payload_hash({"a": 1, "b": [1, 2]}) == payload_hash({"b": [1, 2], "a": 1})


# ---------- RBAC ----------
def test_system_roles_cover_expected_keys():
    for key in ("owner", "admin", "sales_manager", "sales", "finance", "marketing", "viewer"):
        assert key in SYSTEM_ROLES
    assert SETTINGS_WRITE in SYSTEM_ROLES["owner"]
    assert SETTINGS_WRITE not in SYSTEM_ROLES["sales"]


async def test_require_permission_denies(db, tenant):
    from app.core import tenancy
    from app.core.permissions import check

    ctx = tenancy.AuthContext(
        user_id=tenant.id, tenant_id=tenant.id, role_key="sales",
        permissions=frozenset({"leads:read"}),
    )
    tenancy.bind_auth(ctx)
    with pytest.raises(PermissionDenied):
        check(ctx, "settings:write")
    tenancy.clear_auth()


async def test_platform_admin_bypasses(db, tenant):
    from app.core import tenancy

    ctx = tenancy.AuthContext(
        user_id=tenant.id, tenant_id=tenant.id, role_key="owner",
        permissions=frozenset(), is_platform_admin=True,
    )
    assert ctx.has("anything:atall")


# ---------- state machines ----------
def test_illegal_transition_raises_with_context():
    sm = DemoMachine("NEW")
    with pytest.raises(Conflict) as exc:
        sm.fire("finish")
    assert exc.value.details["state"] == "NEW"
    assert "start" in exc.value.details["allowed"]


def test_happy_path_transitions():
    sm = DemoMachine()
    assert sm.state == "NEW"
    sm.fire("start")
    assert sm.state == "RUNNING"
    sm.fire("finish")
    assert sm.state == "DONE"


# ---------- Money ----------
def test_money_arithmetic_and_validation():
    price = Money("8250000.00", "EGP")
    commission = price.pct(2.5)
    assert commission.amount == Money("206250.0000", "EGP").amount
    assert commission.currency == "EGP"
    with pytest.raises(ValueError):
        Money(10, "XXX")
    with pytest.raises(ValueError):
        Money("1", "EGP").add(Money("1", "USD"))


def test_money_never_floats():
    m = Money(0.1 + 0.2, "USD")  # float noise quantized to 4dp
    assert m.amount == Money("0.3000", "USD").amount
