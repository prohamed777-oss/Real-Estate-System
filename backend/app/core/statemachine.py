"""State machines as domain primitives (review rule #3).

Lifecycle states are NOT loose status strings mutated by `if` statements.
Each lifecycle has a declarative machine; illegal transitions raise DomainError.

Usage:
        class LeadStateMachine(StateMachine):
            initial = "NEW"
            transitions = {
                "NEW": {"contact": "CONTACTED", "disqualify": "DISQUALIFIED"},
                ...
            }

        sm = LeadStateMachine(lead.lifecycle_stage)
        lead.lifecycle_stage = sm.fire("contact")
"""

from __future__ import annotations

from typing import Callable

from app.core.errors import Conflict


class StateMachine:
    initial: str = ""
    # state -> {event -> target_state}
    transitions: dict[str, dict[str, str]] = {}
    # states from which no further exit exists
    terminal: set[str] = set()

    def __init__(self, state: str | None = None) -> None:
        self.state = state or self.initial

    def can(self, event: str) -> bool:
        return event in self.transitions.get(self.state, {})

    def fire(self, event: str, *, guard: Callable[[], bool] | None = None) -> str:
        targets = self.transitions.get(self.state, {})
        if event not in targets:
            raise Conflict(
                f"Illegal transition: {self.state} --{event}--> ?",
                details={"state": self.state, "event": event, "allowed": sorted(targets)},
            )
        if guard is not None and not guard():
            raise Conflict(f"Guard rejected transition {self.state} --{event}--> {targets[event]}")
        self.state = targets[event]
        return self.state

    def allowed_events(self) -> list[str]:
        return sorted(self.transitions.get(self.state, {}))
