"""Meta WhatsApp Cloud API adapter (§7).

Production-ready implementation; requires per-tenant credentials stored in
channel_accounts (access token + phone_number_id). The user wires real
credentials at integration time — until then the simulator serves traffic.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from app.channels.gateway import ChannelAdapter, DeliveryResult
from app.core.errors import ExternalProviderError

GRAPH_BASE = "https://graph.facebook.com/v21.0"


class MetaWhatsAppAdapter(ChannelAdapter):
    channel = "whatsapp"
    provider = "meta_whatsapp"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.access_token: str = cfg.get("access_token", "")
        self.phone_number_id: str = cfg.get("phone_number_id", "")
        self.verify_token: str = cfg.get("verify_token", "")
        self.app_secret: str = cfg.get("app_secret", "")

    # ---- outbound ----
    def _headers(self) -> dict[str, str]:
        if not self.access_token or not self.phone_number_id:
            raise ExternalProviderError("Meta WhatsApp credentials not configured for this tenant")
        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

    async def send_text(self, to_ref: str, text: str, *, idempotency_ref: str | None = None) -> DeliveryResult:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_ref,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        return await self._post(payload)

    async def send_media(
        self, to_ref: str, media_url: str, caption: str | None = None, *,
        media_type: str = "image", idempotency_ref: str | None = None,
    ) -> DeliveryResult:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_ref,
            "type": media_type,
            media_type: {"link": media_url},
        }
        if caption:
            payload[media_type]["caption"] = caption
        return await self._post(payload)

    async def _post(self, payload: dict[str, Any]) -> DeliveryResult:
        url = f"{GRAPH_BASE}/{self.phone_number_id}/messages"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ExternalProviderError(f"Meta API unreachable: {exc}") from exc
        if resp.status_code >= 400:
            return DeliveryResult(None, "failed", f"meta_{resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        msg_id = None
        try:
            msg_id = data["messages"][0]["id"]
        except (KeyError, IndexError):
            pass
        return DeliveryResult(msg_id, "sent", raw=data)

    # ---- webhook helpers ----
    @staticmethod
    def verify_webhook_signature(payload_bytes: bytes, signature_header: str, app_secret: str) -> bool:
        if not app_secret or not signature_header:
            return False
        expected = "sha256=" + hmac.new(app_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    @staticmethod
    def parse_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract normalized inbound message dicts from a Meta webhook body."""
        out: list[dict[str, Any]] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                phone_number_id = value.get("metadata", {}).get("phone_number_id")
                for msg in value.get("messages", []) or []:
                    mtype = msg.get("type", "text")
                    text = None
                    attachments: list[dict] = []
                    if mtype == "text":
                        text = msg.get("text", {}).get("body")
                    elif mtype in ("image", "video", "audio", "document", "sticker"):
                        media = msg.get(mtype, {})
                        attachments.append({
                            "type": mtype, "id": media.get("id"),
                            "mime_type": media.get("mime_type"), "filename": media.get("filename"),
                        })
                    elif mtype == "button":
                        text = msg.get("button", {}).get("text")
                    elif mtype == "interactive":
                        reply = msg.get("interactive", {}).get("button_reply", {})
                        text = reply.get("title")
                    contact = (value.get("contacts") or [{}])[0]
                    profile = contact.get("profile", {})
                    out.append({
                        "channel": "whatsapp",
                        "provider": "meta_whatsapp",
                        "external_id": msg.get("id"),
                        "person_ref": msg.get("from"),
                        "person_name": profile.get("name"),
                        "message_type": mtype,
                        "text": text,
                        "attachments": attachments,
                        "phone_number_id": phone_number_id,
                    })
        return out
