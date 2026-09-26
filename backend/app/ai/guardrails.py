"""Guardrails (§60) — the model may not invent transaction-critical facts.

Enforcement layers:
1. TOOLS are the only data source — prices/availability come from the domain.
2. Post-output check: currency amounts or availability claims WITHOUT a
   corresponding tool call are flagged; the response is wrapped in a caveat.
3. The ledger records every flag (AI governance).

This is not a prompt trick — it is a mechanical check on the response vs the
tool-call trace.
"""

from __future__ import annotations

import re
from typing import Any

# e.g. "2,500,000 EGP" / "4.2 مليون" / "8250000 جنيه" / "$300,000"
PRICE_PATTERN = re.compile(
    r"(\d{1,3}(?:[,.]\d{3})+|\d{4,})(\s?(EGP|USD|جنيه|مليون|ج\.م)|\$)", re.IGNORECASE
)
AVAILABILITY_PATTERN = re.compile(
    r"(متاح|متاحة|available|available now| still available|محجوز|sold|تم بيع)", re.IGNORECASE
)

PRICE_TOOLS = {"get_current_price", "search_properties", "get_property"}
AVAILABILITY_TOOLS = {"check_availability", "search_properties"}


def check_output(text: str, tool_calls: list[dict[str, Any]]) -> tuple[list[str], bool]:
    """Returns (flags, needs_human). Flags are recorded in the ledger; when a
    transaction-critical claim has NO tool evidence, the caller is told to
    escalate to a human instead of trusting the message."""
    called = {tc["name"] for tc in (tool_calls or [])}
    flags: list[str] = []
    needs_human = False

    if text and PRICE_PATTERN.search(text) and not (called & PRICE_TOOLS):
        flags.append("price_without_tool_evidence")
        needs_human = True
    if text and AVAILABILITY_PATTERN.search(text) and not (called & AVAILABILITY_TOOLS):
        flags.append("availability_without_tool_evidence")
        needs_human = True
    if re.search(r"(أعطيك خصم|اقدر أعمل لك خصم|أضمن لك|I can give you a discount|guarantee)", text, re.IGNORECASE):
        flags.append("commitment_language")
        needs_human = True
    return flags, needs_human


SAFE_FALLBACK_AR = (
    "للأسف مش هقدر أأكدلك المعلومة دي حاليًا. هحولك فورًا لموظف مختص يؤكدلك "
    "السعر والتوفر بدقة. 🙏"
)


def wrap_if_flagged(message: str, flags: list[str]) -> str:
    if not flags:
        return message
    return f"{message}\n\n⚠️ {SAFE_FALLBACK_AR}"
