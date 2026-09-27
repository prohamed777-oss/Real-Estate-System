"""Channel Gateway (§7) — provider adapters are the ONLY entry/exit points.

Providers never leak into domain services: domains speak normalized
InboundMessage/OutboundMessage; adapters speak provider wire formats.
Adding a provider = adding an adapter class + registration. Nothing else changes.
"""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import DomainError

SUPPORTED_CHANNELS = {"whatsapp", "instagram", "messenger", "email", "sms", "webchat", "voice"}


@dataclass
class InboundMessage:
    """Normalized inbound message from ANY provider."""

    channel: str
    provider: str
    external_id: str | None          # provider message id (dedup)
    person_ref: str                  # channel identity: wa id / email / phone
    person_name: str | None
    message_type: str = "text"       # text | image | audio | document | location
    text: str | None = None
    attachments: list[dict] = field(default_factory=list)
    raw_event_ref: str | None = None  # webhook_events.id


@dataclass
class DeliveryResult:
    external_id: str | None
    status: str  # sent | failed
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(ABC):
    """Base adapter. One instance per tenant connection (created from config)."""

    channel: str = ""
    provider: str = ""

    @abstractmethod
    async def send_text(self, to_ref: str, text: str, *, idempotency_ref: str | None = None) -> DeliveryResult: ...

    @abstractmethod
    async def send_media(
        self, to_ref: str, media_url: str, caption: str | None = None, *,
        media_type: str = "image", idempotency_ref: str | None = None,
    ) -> DeliveryResult: ...


class SimulatorAdapter(ChannelAdapter):
    """Deterministic in-process adapter for development/testing (no network).

    Mirrors the real WhatsApp flow: accepts sends, records them, can simulate
    inbound messages and delivery callbacks. The inbox UI works identically
    whether the traffic came from the simulator or Meta.
    """

    channel = "whatsapp"
    provider = "simulator"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.sent: list[dict[str, Any]] = []
        self.fail_next = False

    async def send_text(self, to_ref: str, text: str, *, idempotency_ref: str | None = None) -> DeliveryResult:
        if self.fail_next:
            self.fail_next = False
            return DeliveryResult(None, "failed", "simulated failure")
        external_id = "sim-" + hashlib.sha256(f"{to_ref}:{text}:{idempotency_ref or ''}".encode()).hexdigest()[:16]
        self.sent.append({"to": to_ref, "text": text, "external_id": external_id, "at": time.time()})
        return DeliveryResult(external_id, "sent")

    async def send_media(
        self, to_ref: str, media_url: str, caption: str | None = None, *,
        media_type: str = "image", idempotency_ref: str | None = None,
    ) -> DeliveryResult:
        external_id = "sim-" + hashlib.sha256(f"{to_ref}:{media_url}:{idempotency_ref or ''}".encode()).hexdigest()[:16]
        self.sent.append({"to": to_ref, "media": media_url, "external_id": external_id, "at": time.time()})
        return DeliveryResult(external_id, "sent")


# --- adapter registry: tenant config -> adapter instance ---
_ADAPTER_FACTORIES: dict[str, type[ChannelAdapter]] = {}


def register_adapter(provider: str, klass: type[ChannelAdapter]) -> None:
    _ADAPTER_FACTORIES[provider] = klass


def get_adapter(provider: str, config: dict[str, Any] | None = None) -> ChannelAdapter:
    klass = _ADAPTER_FACTORIES.get(provider)
    if klass is None:
        raise DomainError(
            f"No adapter registered for provider {provider!r}",
            code="adapter_not_found", status_code=400,
        )
    return klass(config or {})


def _register_defaults() -> None:
    register_adapter("simulator", SimulatorAdapter)

    from app.channels.meta_whatsapp import MetaWhatsAppAdapter

    register_adapter("meta_whatsapp", MetaWhatsAppAdapter)


_register_defaults()
