"""Lead state machine (§9) — domain primitive, not status strings."""

from __future__ import annotations

from app.core.statemachine import StateMachine


class LeadStateMachine(StateMachine):
    initial = "NEW"
    transitions = {
        "NEW": {"contact": "CONTACTED", "disqualify": "DISQUALIFIED", "mark_dormant": "DORMANT"},
        "CONTACTED": {"qualify": "QUALIFYING", "disqualify": "DISQUALIFIED", "mark_dormant": "DORMANT"},
        "QUALIFYING": {"qualified": "QUALIFIED", "disqualify": "DISQUALIFIED", "mark_dormant": "DORMANT"},
        "QUALIFIED": {"nurture": "NURTURE", "convert": "CONVERTED",
                      "disqualify": "DISQUALIFIED", "mark_dormant": "DORMANT"},
        "NURTURE": {"reactivate": "QUALIFIED", "convert": "CONVERTED", "disqualify": "DISQUALIFIED"},
        "DORMANT": {"reactivate": "CONTACTED", "disqualify": "DISQUALIFIED"},
        "CONVERTED": {},
        "DISQUALIFIED": {"reactivate": "CONTACTED"},
    }
    terminal = {"CONVERTED"}

    # side states that are recoverable, not terminal
    RECOVERABLE = {"DORMANT", "DISQUALIFIED"}


LEAD_EVENTS = {
    "contact": "lead.contacted",
    "qualify": "lead.qualifying",
    "qualified": "lead.qualified",
    "nurture": "lead.nurtured",
    "convert": "lead.converted",
    "disqualify": "lead.disqualified",
    "mark_dormant": "lead.dormant",
    "reactivate": "lead.reactivated",
}
