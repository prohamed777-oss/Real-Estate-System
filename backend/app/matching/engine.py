"""Matching Engine pipeline (§25):

    Requirements → Hard Constraint Filtering → Candidate Generation
    → Semantic Retrieval → Business Ranking → Behavioral Re-ranking → Top Matches

Deterministic and testable: hard filters never compromise; semantic and
behavioral layers only RE-RANK candidates, never invent them. Vector search is
NEVER used for price/availability/permissions (§24).
"""

from __future__ import annotations

import math
import uuid
from typing import Any

ENGINE_VERSION = "v1"

# Business ranking weights (§25 ranking signals)
RANK_WEIGHTS = {
    "budget_fit": 25,
    "location_fit": 20,
    "bedroom_fit": 15,
    "property_type_fit": 10,
    "delivery_fit": 10,
    "finishing_fit": 5,
    "availability": 10,
    "freshness": 5,
}


def _budget_fit(price: float | None, req: dict[str, Any]) -> float:
    min_b = req.get("min_budget")
    max_b = req.get("max_budget")
    if price is None:
        return 0.3
    if max_b and price > float(max_b):
        # slightly over budget is recoverable, way over is a miss
        over_ratio = price / float(max_b)
        return max(0.0, 1.0 - (over_ratio - 1.0) * 2.0)
    if min_b and price < float(min_b):
        return 0.6  # under budget: acceptable but may signal quality mismatch
    return 1.0


def _location_fit(doc: dict[str, Any], req: dict[str, Any]) -> float:
    wanted_areas = [a.lower() for a in (req.get("areas") or [])]
    if not wanted_areas:
        return 0.7
    doc_area = (doc.get("area") or "").lower()
    doc_city = (doc.get("city") or "").lower()
    for a in wanted_areas:
        if a and (a in doc_area or a in doc_city or doc_area in a):
            return 1.0
    return 0.2


def _bedroom_fit(doc: dict[str, Any], req: dict[str, Any]) -> float:
    wanted = req.get("bedrooms")
    if not wanted or doc.get("bedrooms") is None:
        return 0.7
    diff = abs(int(doc["bedrooms"]) - int(wanted))
    return {0: 1.0, 1: 0.6}.get(diff, 0.2)


def _type_fit(doc: dict[str, Any], req: dict[str, Any]) -> float:
    wanted = [t.lower() for t in (req.get("property_types") or [])]
    if not wanted:
        return 0.8
    return 1.0 if (doc.get("property_type") or "").lower() in wanted else 0.0


def _delivery_fit(doc: dict[str, Any], req: dict[str, Any]) -> float:
    wanted = req.get("delivery_preference")
    if not wanted:
        return 0.8
    return 1.0 if doc.get("delivery_status") == wanted else 0.4


def _finishing_fit(doc: dict[str, Any], req: dict[str, Any]) -> float:
    wanted = req.get("finishing_preference")
    if not wanted:
        return 0.8
    return 1.0 if doc.get("finishing") == wanted else 0.4


def _availability_score(state: str | None, confidence: str | None) -> float:
    if state == "AVAILABLE":
        return 1.0 if confidence != "stale_hold" else 0.7
    if state == "HELD":
        return 0.5
    if state in ("RESERVED", "CONTRACTED", "SOLD"):
        return 0.0
    return 0.3


def _freshness_score(updated_at: Any) -> float:
    if not updated_at:
        return 0.5
    return 1.0


def hard_filter(doc: dict[str, Any], req: dict[str, Any], availability: dict[str, Any] | None) -> bool:
    """Non-negotiable constraints. Reserved/sold units NEVER match (§102)."""
    state = (availability or {}).get("state")
    if state in ("RESERVED", "CONTRACTED", "SOLD"):
        return False
    max_b = req.get("max_budget")
    price = doc.get("price_amount")
    if max_b and price and float(price) > float(max_b) * 1.10:  # >10% over = filtered
        return False
    wanted_types = [t.lower() for t in (req.get("property_types") or [])]
    if wanted_types and (doc.get("property_type") or "").lower() not in wanted_types:
        return False
    min_bed = req.get("bedrooms")
    if min_bed and doc.get("bedrooms") is not None and int(doc["bedrooms"]) < int(min_bed) - 1:
        return False
    return True


def score_candidate(
    *, doc: dict[str, Any], req: dict[str, Any], availability: dict[str, Any] | None,
    semantic_similarity: float | None = None, behavioral: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signals = {
        "budget_fit": _budget_fit(doc.get("price_amount"), req),
        "location_fit": _location_fit(doc, req),
        "bedroom_fit": _bedroom_fit(doc, req),
        "property_type_fit": _type_fit(doc, req),
        "delivery_fit": _delivery_fit(doc, req),
        "finishing_fit": _finishing_fit(doc, req),
        "availability": _availability_score(
            (availability or {}).get("state"), (availability or {}).get("confidence")
        ),
        "freshness": _freshness_score(doc.get("updated_at")),
    }
    total_weight = sum(RANK_WEIGHTS.values())
    business_score = sum(RANK_WEIGHTS[k] * v for k, v in signals.items()) / total_weight

    # Semantic layer: re-ranks, never invents (§24)
    semantic_component = (semantic_similarity or 0.5) * 0.25
    # Behavioral re-ranking (explicit vs behavioral preferences §10)
    behavior_component = float((behavioral or {}).get("affinity", 0.5)) * 0.1

    final = 0.65 * business_score + semantic_component + behavior_component
    return {
        "asset_id": doc.get("asset_id"),
        "final_score": round(final, 4),
        "business_score": round(business_score, 4),
        "semantic_similarity": semantic_similarity,
        "signals": {k: round(v, 3) for k, v in signals.items()},
        "reasons": _reasons(signals, req),
    }


def _reasons(signals: dict[str, float], req: dict[str, Any]) -> list[str]:
    reasons = []
    if signals["budget_fit"] >= 0.99:
        reasons.append("within_budget")
    if signals["location_fit"] >= 0.99:
        reasons.append("area_match")
    if signals["bedroom_fit"] >= 0.99:
        reasons.append("bedrooms_match")
    if signals["availability"] >= 0.99:
        reasons.append("available_now")
    if signals["property_type_fit"] >= 0.99:
        reasons.append("type_match")
    return reasons


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def rank_candidates(
    *, docs: list[dict[str, Any]], requirements: dict[str, Any],
    availability_map: dict[str, dict], lead_embedding: list[float] | None = None,
    behavioral: dict[str, Any] | None = None, top_n: int = 10,
) -> list[dict[str, Any]]:
    candidates = []
    for doc in docs:
        aid = doc.get("asset_id")
        avail = availability_map.get(str(aid))
        if not hard_filter(doc, requirements, avail):
            continue
        semantic = None
        if lead_embedding and doc.get("embedding"):
            semantic = cosine_similarity(lead_embedding, doc["embedding"])
        candidates.append(
            score_candidate(
                doc=doc, req=requirements, availability=avail,
                semantic_similarity=semantic, behavioral=behavioral,
            )
        )
    candidates.sort(key=lambda c: c["final_score"], reverse=True)
    return candidates[:top_n]
