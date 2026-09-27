"""Unified Inbox API (§89 Inbox): conversations, messages, tasks."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import Conversation, Message, Task
from app.conversations.service import assign_conversation, get_conversation
from app.core.db import get_session
from app.core.errors import NotFound, ValidationFailed
from app.core.permissions import CONVERSATIONS_READ, CONVERSATIONS_WRITE, require
from app.core.tenancy import AuthContext
from app.identity.models import Person

router = APIRouter(tags=["inbox"])


@router.get("/conversations")
async def list_conversations(
    auth: AuthContext = Depends(require(CONVERSATIONS_READ)),
    session: AsyncSession = Depends(get_session),
    status: str | None = None,
    channel: str | None = None,
    assigned_to_me: bool = False,
    limit: int = Query(default=50, le=100),
) -> list[dict[str, Any]]:
    query = (
        select(Conversation, Person)
        .join(Person, Person.id == Conversation.person_id)
        .where(Conversation.tenant_id == auth.tenant_id)
        .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.created_at.desc())
        .limit(limit)
    )
    if status:
        query = query.where(Conversation.status == status)
    if channel:
        query = query.where(Conversation.channel == channel)
    if assigned_to_me:
        query = query.where(Conversation.assigned_user_id == auth.user_id)
    # Branch/Team scoping: sales sees only their assigned conversations
    if auth.role_key == "sales" and not assigned_to_me:
        query = query.where(Conversation.assigned_user_id == auth.user_id)
    rows = (await session.execute(query)).all()
    return [
        {
            "id": str(c.id), "person_id": str(c.person_id), "person_name": p.full_name,
            "person_phone": p.phone, "channel": c.channel, "provider": c.provider,
            "status": c.status, "assigned_user_id": str(c.assigned_user_id) if c.assigned_user_id else None,
            "unread_count": c.unread_count, "last_message_preview": c.last_message_preview,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
            "ai_handling": c.ai_handling,
        }
        for c, p in rows
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation_detail(
    conversation_id: uuid.UUID,
    auth: AuthContext = Depends(require(CONVERSATIONS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    conv = await get_conversation(session, auth.tenant_id, conversation_id)
    messages = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .limit(200)
        )
    ).scalars().all()
    conv.unread_count = 0  # viewed
    return {
        "id": str(conv.id), "person_id": str(conv.person_id), "channel": conv.channel,
        "status": conv.status, "assigned_user_id": str(conv.assigned_user_id) if conv.assigned_user_id else None,
        "messages": [
            {
                "id": str(m.id), "direction": m.direction, "sender_type": m.sender_type,
                "text": m.text, "message_type": m.message_type, "attachments": m.attachments,
                "status": m.status, "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


class AssignIn(BaseModel):
    user_id: uuid.UUID | None = None
    to_me: bool = False


@router.post("/conversations/{conversation_id}/assign")
async def assign(
    conversation_id: uuid.UUID,
    body: AssignIn,
    auth: AuthContext = Depends(require(CONVERSATIONS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    conv = await get_conversation(session, auth.tenant_id, conversation_id)
    target = auth.user_id if body.to_me else body.user_id
    if target is None:
        raise ValidationFailed("user_id or to_me required")
    await assign_conversation(
        session, conversation=conv, assigned_to=target,
        assigned_by=str(auth.user_id), reason="manual assign",
    )
    return {"conversation_id": str(conv.id), "assigned_to": str(target)}


class ReplyIn(BaseModel):
    text: str


@router.post("/conversations/{conversation_id}/reply")
async def reply(
    conversation_id: uuid.UUID,
    body: ReplyIn,
    auth: AuthContext = Depends(require(CONVERSATIONS_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    from app.channels.service import send_outbound_message

    return await send_outbound_message(
        session, tenant_id=auth.tenant_id, conversation_id=conversation_id,
        sender_type="user", sender_id=str(auth.user_id), text=body.text,
    )


# ---------- Tasks ----------
class TaskIn(BaseModel):
    title: str
    description: str | None = None
    type: str = "follow_up"
    priority: str = "normal"
    due_at: str | None = None
    assignee_id: uuid.UUID | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None


@router.get("/tasks")
async def list_tasks(
    auth: AuthContext = Depends(require(CONVERSATIONS_READ)),
    session: AsyncSession = Depends(get_session),
    status: str = "open",
    mine: bool = False,
) -> list[dict[str, Any]]:
    query = select(Task).where(Task.tenant_id == auth.tenant_id, Task.status == status)
    if mine:
        query = query.where(Task.assignee_id == auth.user_id)
    rows = (await session.execute(query.order_by(Task.due_at.nullslast()).limit(100))).scalars().all()
    return [
        {
            "id": str(t.id), "title": t.title, "description": t.description, "type": t.type,
            "priority": t.priority, "status": t.status,
            "due_at": t.due_at.isoformat() if t.due_at else None,
            "assignee_id": str(t.assignee_id) if t.assignee_id else None,
            "entity_type": t.entity_type, "entity_id": str(t.entity_id) if t.entity_id else None,
        }
        for t in rows
    ]


@router.post("/tasks", status_code=201)
async def create_task(
    body: TaskIn,
    auth: AuthContext = Depends(require(CONVERSATIONS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from datetime import datetime

    task = Task(
        tenant_id=auth.tenant_id, title=body.title, description=body.description,
        type=body.type, priority=body.priority,
        due_at=datetime.fromisoformat(body.due_at) if body.due_at else None,
        assignee_id=body.assignee_id or auth.user_id, created_by=str(auth.user_id),
        entity_type=body.entity_type, entity_id=body.entity_id,
    )
    session.add(task)
    await session.flush()
    return {"id": str(task.id), "title": task.title, "status": task.status}


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: uuid.UUID,
    auth: AuthContext = Depends(require(CONVERSATIONS_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from datetime import UTC, datetime

    task = (
        await session.execute(select(Task).where(Task.id == task_id, Task.tenant_id == auth.tenant_id))
    ).scalar_one_or_none()
    if task is None:
        raise NotFound("Task not found")
    task.status = "done"
    task.completed_at = datetime.now(UTC)
    return {"id": str(task.id), "status": task.status}
