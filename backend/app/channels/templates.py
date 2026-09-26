"""WhatsApp templates: render variables + send through the channel gateway.

Meta requires pre-approved templates for business-initiated conversations —
without this, production WhatsApp messaging would be rejected.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.models import MessageTemplate
from app.core.errors import Conflict, NotFound, ValidationFailed

VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def extract_variables(body: str) -> list[str]:
    return sorted(set(VAR_PATTERN.findall(body)))


def render_template(body: str, variables: dict[str, Any]) -> str:
    missing = [v for v in extract_variables(body) if v not in variables]
    if missing:
        raise ValidationFailed(
            "Missing template variables", details={"missing": missing}
        )
    return VAR_PATTERN.sub(lambda m: str(variables[m.group(1)]), body)


async def create_template(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str, body: str,
    language: str = "ar", category: str = "UTILITY", status: str = "approved",
    created_by: str | None = None,
) -> MessageTemplate:
    if not body.strip():
        raise ValidationFailed("Template body required")
    template = MessageTemplate(
        tenant_id=tenant_id, name=name, language=language, category=category,
        body=body, variables=extract_variables(body), status=status,
        created_by=created_by,
    )
    session.add(template)
    await session.flush()
    return template


async def get_template(session: AsyncSession, *, tenant_id: uuid.UUID,
                       name: str, language: str = "ar") -> MessageTemplate:
    row = (
        await session.execute(
            select(MessageTemplate).where(
                MessageTemplate.tenant_id == tenant_id,
                MessageTemplate.name == name,
                MessageTemplate.language == language,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(f"Template not found: {name}")
    return row


async def send_templated_message(
    session: AsyncSession, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID,
    template_name: str, variables: dict[str, Any], language: str = "ar",
    sender_type: str = "system", actor_id=None,
) -> dict[str, Any]:
    """Render an approved template and send through the normal pipeline
    (consent gate still applies — templates are not a consent bypass)."""
    from app.channels.service import send_outbound_message

    template = await get_template(session, tenant_id=tenant_id, name=template_name,
                                  language=language)
    if template.status != "approved":
        raise Conflict(f"Template {template.name!r} is {template.status}, not approved")
    text = render_template(template.body, variables)
    return await send_outbound_message(
        session, tenant_id=tenant_id, conversation_id=conversation_id,
        sender_type=sender_type, sender_id=str(actor_id) if actor_id else "template",
        text=text,
    )
