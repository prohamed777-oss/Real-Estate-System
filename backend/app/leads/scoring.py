"""Scoring Engine (review rule #2): ONE engine, composable signals.

Outputs (§11): lead_score, intent_score, engagement_score, fit_score.
Every calculation records: score_version, signals_used, calculated_at —
so six months from now we know WHY a lead scored 87.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

SCORE_VERSION = "v1-heuristic"

SIGNAL_WEIGHTS: dict[str, dict[str, int]] = {
    "intent": {
        "explicit_budget": 30,
        "stated_timeline": 20,
        "asked_about_specific_property": 15,
        "requested_viewing": 25,
        "negotiation_language": 10,
    },
    "engagement": {
        "messages_last_7d": 25,
        "inbound_reply_rate": 20,
        "calls_answered": 15,
        "viewings_attended": 25,
        "active_days_last_14d": 15,
    },
    "fit": {
        "matches_available_inventory": 40,
        "budget_matches_listings": 30,
        "area_matches_inventory": 20,
        "type_matches_inventory": 10,
    },
}

BUSINESS_RULES: dict[str, int] = {
    "sla_breach_open": -15,
    "owner_assigned": 5,
    "source_referral": 10,
    "source_paid_ads": 5,
    "dormant_over_30d": -20,
}


def _signals_to_score(signals: dict[str, Any], weights: dict[str, int]) -> int:
    score = 0
    for key, weight in weights.items():
        value = signals.get(key)
        if value in (None, False, 0, "", []):
            continue
        if isinstance(value, bool):
            score += weight
        elif isinstance(value, (int, float)):
            # numeric signals scale with magnitude, capped by weight
            score += min(weight, int(weight * min(1.0, float(value))))
        else:
            score += weight
    return max(0, min(100, score))


def compute_scores(
    *,
    lead: dict[str, Any],
    requirements: dict[str, Any],
    activity: dict[str, Any],
    inventory_fit: dict[str, Any],
) -> dict[str, Any]:
    """Compose signals into the four scores. Deterministic, versioned, auditable."""
    intent_signals = {
        "explicit_budget": bool(requirements.get("explicit", {}).get("max_budget")),
        "stated_timeline": bool(requirements.get("timeline")),
        "asked_about_specific_property": activity.get("asked_about_specific_property", 0) > 0,
        "requested_viewing": activity.get("requested_viewing", 0) > 0,
        "negotiation_language": activity.get("negotiation_signals", 0) > 0,
    }
    engagement_signals = {
        "messages_last_7d": activity.get("messages_last_7d", 0),
        "inbound_reply_rate": activity.get("inbound_reply_rate", 0),
        "calls_answered": activity.get("calls_answered", 0),
        "viewings_attended": activity.get("viewings_attended", 0),
        "active_days_last_14d": activity.get("active_days_last_14d", 0),
    }
    fit_signals = {
        "matches_available_inventory": inventory_fit.get("matches_available", 0) > 0,
        "budget_matches_listings": inventory_fit.get("budget_matches", 0) > 0,
        "area_matches_inventory": inventory_fit.get("area_matches", 0) > 0,
        "type_matches_inventory": inventory_fit.get("type_matches", 0) > 0,
    }
    business = {
        "owner_assigned": bool(lead.get("owner_id")),
        "source_referral": lead.get("source") == "referral",
        "source_paid_ads": lead.get("source") in ("paid_ads", "meta_ads", "google_ads"),
        "sla_breach_open": bool(lead.get("sla_breached")),
        "dormant_over_30d": bool(lead.get("dormant_over_30d")),
    }

    intent_score = _signals_to_score(intent_signals, SIGNAL_WEIGHTS["intent"])
    engagement_score = _signals_to_score(engagement_signals, SIGNAL_WEIGHTS["engagement"])
    fit_score = _signals_to_score(fit_signals, SIGNAL_WEIGHTS["fit"])
    business_adjust = sum(v for v in BUSINESS_RULES.values() if False)  # placeholder, replaced below
    business_adjust = 0
    for key, adj in BUSINESS_RULES.items():
        if business.get(key):
            business_adjust += adj

    lead_score = max(0, min(100, int(0.4 * intent_score + 0.3 * engagement_score + 0.3 * fit_score) + business_adjust))

    return {
        "lead_score": lead_score,
        "intent_score": intent_score,
        "engagement_score": engagement_score,
        "fit_score": fit_score,
        "score_version": SCORE_VERSION,
        "score_signals": {
            "intent": intent_signals,
            "engagement": engagement_signals,
            "fit": fit_signals,
            "business": business,
            "calculated_at": datetime.now(UTC).isoformat(),
        },
        "score_calculated_at": datetime.now(UTC),
    }
