"""Inventory state machine (§17) — unit lifecycle is a domain primitive.

AVAILABLE → HELD → RESERVED → CONTRACTED → SOLD
side: BLOCKED / RELEASED / EXPIRED

Invariant (§102): one unit can never have two active reservations.
Enforcement: transactional claim with FOR UPDATE on unit_inventory row.
"""

from __future__ import annotations

from app.core.statemachine import StateMachine


class InventoryStateMachine(StateMachine):
    initial = "AVAILABLE"
    transitions = {
        "AVAILABLE": {"hold": "HELD", "block": "BLOCKED", "reserve": "RESERVED"},
        "HELD": {"reserve": "RESERVED", "release": "AVAILABLE", "expire": "EXPIRED"},
        "RESERVED": {"contract": "CONTRACTED", "release": "AVAILABLE", "cancel": "AVAILABLE"},
        "CONTRACTED": {"complete": "SOLD", "terminate": "AVAILABLE"},
        "BLOCKED": {"release": "AVAILABLE"},
        "EXPIRED": {"hold": "HELD", "reserve": "RESERVED", "release": "AVAILABLE"},
        "RELEASED": {},
        "SOLD": {},
    }
    terminal = {"SOLD"}


INVENTORY_EVENTS = {
    "hold": "unit.held",
    "release": "unit.released",
    "expire": "unit.expired",
    "reserve": "unit.reserved",
    "contract": "unit.contracted",
    "complete": "unit.sold",
    "block": "unit.blocked",
    "cancel": "unit.cancelled",
    "terminate": "unit.released",
}


class ListingStateMachine(StateMachine):
    initial = "draft"
    transitions = {
        "draft": {"activate": "active", "archive": "archived"},
        "active": {"pause": "paused", "expire": "expired", "archive": "archived"},
        "paused": {"activate": "active", "archive": "archived"},
        "expired": {"activate": "active", "archive": "archived"},
    }
    terminal = {"archived"}
