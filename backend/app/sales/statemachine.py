"""Sales state machines (§27, §31, §33) + reservation states (§35)."""

from __future__ import annotations

from app.core.statemachine import StateMachine


class OpportunityStateMachine(StateMachine):
    initial = "DISCOVERY"
    transitions = {
        "DISCOVERY": {"qualify": "QUALIFIED", "lose": "LOST"},
        "QUALIFIED": {"match": "MATCHED", "lose": "LOST"},
        "MATCHED": {"view": "VIEWING", "lose": "LOST"},
        "VIEWING": {"offer": "OFFER", "back_to_match": "MATCHED", "lose": "LOST"},
        "OFFER": {"negotiate": "NEGOTIATION", "back_to_view": "VIEWING", "lose": "LOST"},
        "NEGOTIATION": {"reserve": "RESERVATION", "back_to_offer": "OFFER", "lose": "LOST"},
        "RESERVATION": {"win": "WON", "lose": "LOST"},
        "WON": {},
        "LOST": {},
    }
    terminal = {"WON", "LOST"}


OPPORTUNITY_EVENTS = {
    "qualify": "opportunity.qualified",
    "match": "opportunity.matched",
    "view": "opportunity.viewing",
    "offer": "opportunity.offer",
    "negotiate": "opportunity.negotiation",
    "reserve": "opportunity.reservation",
    "win": "opportunity.won",
    "lose": "opportunity.lost",
    "back_to_match": "opportunity.stage_changed",
    "back_to_view": "opportunity.stage_changed",
    "back_to_offer": "opportunity.stage_changed",
}


class ViewingStateMachine(StateMachine):
    initial = "REQUESTED"
    transitions = {
        "REQUESTED": {"confirm": "CONFIRMED", "cancel": "CANCELLED"},
        "CONFIRMED": {"attend": "ATTENDED", "reschedule": "CONFIRMED", "cancel": "CANCELLED",
                       "no_show": "NO_SHOW"},
        "ATTENDED": {"complete": "COMPLETED"},
        "COMPLETED": {},
        "CANCELLED": {},
        "NO_SHOW": {"reschedule": "CONFIRMED", "complete": "COMPLETED"},
    }
    terminal = {"COMPLETED", "CANCELLED"}


class OfferStateMachine(StateMachine):
    initial = "DRAFT"
    transitions = {
        "DRAFT": {"submit_approval": "PENDING_APPROVAL", "send": "SENT", "withdraw": "WITHDRAWN"},
        "PENDING_APPROVAL": {"approve": "DRAFT", "send": "SENT", "reject": "REJECTED"},
        "SENT": {"counter": "COUNTERED", "accept": "ACCEPTED", "reject": "REJECTED",
                  "expire": "EXPIRED", "withdraw": "WITHDRAWN"},
        "COUNTERED": {"accept": "ACCEPTED", "reject": "REJECTED", "expire": "EXPIRED",
                       "recount": "SENT"},
        "ACCEPTED": {},
        "REJECTED": {},
        "EXPIRED": {},
        "WITHDRAWN": {},
    }
    terminal = {"ACCEPTED", "REJECTED", "EXPIRED", "WITHDRAWN"}


class ReservationStateMachine(StateMachine):
    initial = "ACTIVE"
    transitions = {
        "ACTIVE": {"confirm": "CONFIRMED", "cancel": "CANCELLED", "expire": "EXPIRED",
                    "convert": "CONVERTED"},
        "CONFIRMED": {"convert": "CONVERTED", "cancel": "CANCELLED", "expire": "EXPIRED"},
        "CONVERTED": {},
        "CANCELLED": {},
        "EXPIRED": {},
    }
    terminal = {"CONVERTED", "CANCELLED", "EXPIRED"}


class DealStateMachine(StateMachine):
    initial = "OPEN"
    transitions = {
        "OPEN": {"contract": "CONTRACTED", "lose": "LOST", "cancel": "CANCELLED"},
        "CONTRACTED": {"win": "WON", "lose": "LOST", "cancel": "CANCELLED"},
    }
    terminal = {"WON", "LOST", "CANCELLED"}
