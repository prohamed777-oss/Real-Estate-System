"""Automation services (§43-44, §29): rules engine + durable journeys + SLA tick.

Journey durability: every step and wait is a DB row. A crash between steps
loses nothing — the tick resumes from `current_step` and `wait_until`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.models import (
    AutomationRule,
    Journey,
    JourneyInstance,
    JourneyStepLog,
    Notification,
    SLAPolicy,
    SLATracker,
)
from app.core.errors import NotFound
from app.events.outbox import emit

log = logging.getLogger("revenue_os.automation")


# ---------- rules engine ----------
async def apply_rules_for_event(
    session: AsyncSession, *, tenant_id: uuid.UUID, event_name: str,
    payload: dict[str, Any],
) -> int:
    rules = (
        await session.execute(
            select(AutomationRule).where(
                AutomationRule.tenant_id == tenant_id,
                AutomationRule.trigger_event == event_name,
                AutomationRule.is_active.is_(True),
            )
        )
    ).scalars().all()
    executed = 0
    for rule in sorted(rules, key=lambda r: r.priority):
        if not _conditions_match(rule.conditions or {}, payload):
            continue
        for action in rule.actions or []:
            await _execute_action(session, tenant_id=tenant_id, action=action,
                                  event_payload=payload, rule_name=rule.name)
            executed += 1
    return executed


def _conditions_match(conditions: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Tiny declarative matcher: {"field": value, "field.gte": x, "field.in": [...]}."""
    for key, expected in (conditions or {}).items():
        if "." in key:
            field, op = key.rsplit(".", 1)
        else:
            field, op = key, "eq"
        actual = _dig(payload, field)
        if op == "eq" and actual != expected:
            return False
        if op == "neq" and actual == expected:
            return False
        if op == "in" and actual not in (expected or []):
            return False
        if op == "gte" and (actual is None or actual < expected):
            return False
        if op == "lte" and (actual is None or actual > expected):
            return False
        if op == "exists":
            if bool(actual) != bool(expected):
                return False
    return True


def _dig(payload: dict[str, Any], dotted: str) -> Any:
    current: Any = payload
    for part in dotted.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


async def _execute_action(session: AsyncSession, *, tenant_id: uuid.UUID,
                          action: dict[str, Any], event_payload: dict[str, Any],
                          rule_name: str) -> None:
    kind = action.get("type")
    params = action.get("params", {})
    if kind == "notify":
        session.add(Notification(
            tenant_id=tenant_id, kind=params.get("kind", "in_app"),
            recipient_type=params.get("recipient_type", "user"),
            recipient_id=params.get("recipient_id"),
            title=params.get("title", f"Automation: {rule_name}"),
            body=params.get("body", ""),
            payload={"event": event_payload},
            related_entity_type=params.get("entity_type"),
            related_entity_id=uuid.UUID(params["entity_id"]) if params.get("entity_id") else None,
        ))
    elif kind == "create_task":
        from app.conversations.models import Task

        session.add(Task(
            tenant_id=tenant_id, title=params.get("title", rule_name)[:300],
            type=params.get("task_type", "follow_up"),
            priority=params.get("priority", "normal"),
            due_at=datetime.now(UTC) + timedelta(minutes=params.get("due_in_minutes", 60)),
            created_by="automation",
            entity_type=params.get("entity_type"),
            entity_id=uuid.UUID(params["entity_id"]) if params.get("entity_id") else None,
        ))
    elif kind == "emit_event":
        await emit(
            session, event_name=params["event_name"], tenant_id=tenant_id,
            payload={**event_payload, **params.get("extra", {})},
            causation_id=f"rule:{rule_name}",
        )
    elif kind == "start_journey":
        await start_journey(
            session, tenant_id=tenant_id, journey_key=params["journey_key"],
            entity_type=params.get("entity_type", "lead"),
            entity_id=uuid.UUID(event_payload.get("lead_id") or params["entity_id"]),
            context={"event": event_payload},
        )


# ---------- durable journeys ----------
async def start_journey(
    session: AsyncSession, *, tenant_id: uuid.UUID, journey_key: str, entity_type: str,
    entity_id: uuid.UUID, context: dict[str, Any] | None = None,
) -> JourneyInstance:
    journey = (
        await session.execute(
            select(Journey).where(
                Journey.tenant_id == tenant_id, Journey.name == journey_key,
                Journey.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if journey is None:
        raise NotFound(f"Journey not found: {journey_key}")
    instance = JourneyInstance(
        tenant_id=tenant_id, journey_id=journey.id, entity_type=entity_type,
        entity_id=entity_id, context=context or {},
    )
    session.add(instance)
    await session.flush()
    return instance


async def tick_journeys(session: AsyncSession, *, batch: int = 50) -> int:
    """Advance waiting/running journeys whose wait_until has passed."""
    now = datetime.now(UTC)
    instances = (
        await session.execute(
            select(JourneyInstance)
            .where(
                JourneyInstance.status.in_(("running", "waiting")),
                (JourneyInstance.wait_until.is_(None)) | (JourneyInstance.wait_until <= now),
            )
            .order_by(JourneyInstance.started_at)
            .limit(batch)
        )
    ).scalars().all()
    advanced = 0
    for instance in instances:
        try:
            await _advance_instance(session, instance)
            advanced += 1
        except Exception as exc:  # noqa: BLE001
            instance.status = "failed"
            instance.last_error = f"{type(exc).__name__}: {exc}"[:500]
            log.warning("journey instance %s failed: %s", instance.id, exc)
    await session.flush()
    return advanced


async def _advance_instance(session: AsyncSession, instance: JourneyInstance) -> None:
    journey = await session.get(Journey, instance.journey_id)
    steps = journey.definition or []
    instance.status = "running"
    while instance.current_step < len(steps):
        step = steps[instance.current_step]
        step_type = step.get("type")
        if step_type == "wait":
            duration = step.get("duration_minutes", 0)
            instance.wait_until = datetime.now(UTC) + timedelta(minutes=duration)
            instance.current_step += 1
            if duration > 0:
                instance.status = "waiting"
                session.add(JourneyStepLog(
                    tenant_id=instance.tenant_id, instance_id=instance.id,
                    step_no=instance.current_step - 1, step_type="wait",
                    payload={"duration_minutes": duration},
                ))
                return
            continue
        # action step
        await apply_rules_for_event(
            session, tenant_id=instance.tenant_id, event_name="journey.step",
            payload={"instance_id": str(instance.id),
                      "entity_type": instance.entity_type,
                      "entity_id": str(instance.entity_id),
                      "step": step, "context": instance.context},
        )
        # journey steps express actions directly too
        for action in ([step] if step_type not in ("conditions",) else []):
            await _execute_action(
                session, tenant_id=instance.tenant_id, action=action,
                event_payload={"entity_id": str(instance.entity_id),
                                "context": instance.context},
                rule_name=journey.name,
            )
        # idempotency: check if this step was already logged (crash recovery)
        from sqlalchemy import select as _sel

        existing_log = (
            await session.execute(
                _sel(JourneyStepLog).where(
                    JourneyStepLog.instance_id == instance.id,
                    JourneyStepLog.step_no == instance.current_step,
                )
            )
        ).scalar_one_or_none()
        if existing_log is None:
            session.add(JourneyStepLog(
                tenant_id=instance.tenant_id, instance_id=instance.id,
                step_no=instance.current_step, step_type=step_type or "action",
                payload=step.get("params", {}),
            ))
        instance.current_step += 1
    instance.status = "completed"
    instance.finished_at = datetime.now(UTC)


# ---------- SLA engine (§29) ----------
async def start_sla_tracker(
    session: AsyncSession, *, tenant_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID,
    trigger_event: str,
) -> list[SLATracker]:
    policies = (
        await session.execute(
            select(SLAPolicy).where(
                SLAPolicy.tenant_id == tenant_id, SLAPolicy.trigger_event == trigger_event,
                SLAPolicy.is_active.is_(True),
            )
        )
    ).scalars().all()
    trackers = []
    for policy in policies:
        tracker = SLATracker(
            tenant_id=tenant_id, policy_id=policy.id, entity_type=entity_type,
            entity_id=entity_id, due_at=datetime.now(UTC) + timedelta(seconds=policy.target_seconds),
        )
        session.add(tracker)
        trackers.append(tracker)
    await session.flush()
    return trackers


async def tick_sla(session: AsyncSession) -> dict[str, int]:
    """Deterministic SLA evaluation (§29): warn → breach → escalate."""
    now = datetime.now(UTC)
    stats = {"warned": 0, "breached": 0, "escalated": 0}
    trackers = (
        await session.execute(
            select(SLATracker).where(
                SLATracker.status.in_(("on_track", "warned")),
                SLATracker.due_at <= now + timedelta(minutes=60),
            ).limit(200)
        )
    ).scalars().all()
    for tracker in trackers:
        policy = await session.get(SLAPolicy, tracker.policy_id)
        if tracker.due_at <= now and tracker.status != "breached":
            tracker.status = "breached"
            tracker.breached_at = now
            stats["breached"] += 1
            for escalation in policy.escalations if policy else []:
                await _execute_action(
                    session, tenant_id=tracker.tenant_id, action=escalation,
                    event_payload={"entity_type": tracker.entity_type,
                                    "entity_id": str(tracker.entity_id),
                                    "sla": "breached"},
                    rule_name=f"sla:{policy.name if policy else tracker.policy_id}",
                )
                stats["escalated"] += 1
        elif policy and policy.warn_seconds and tracker.status == "on_track":
            warn_at = tracker.due_at - timedelta(seconds=policy.warn_seconds)
            if now >= warn_at:
                tracker.status = "warned"
                stats["warned"] += 1
    await session.flush()
    return stats


async def resolve_sla(session: AsyncSession, *, entity_type: str, entity_id: uuid.UUID,
                      met: bool = True) -> int:
    trackers = (
        await session.execute(
            select(SLATracker).where(
                SLATracker.entity_type == entity_type, SLATracker.entity_id == entity_id,
                SLATracker.status.in_(("on_track", "warned", "breached")),
            )
        )
    ).scalars().all()
    for tracker in trackers:
        tracker.status = "met" if met else "resolved"
        tracker.resolved_at = datetime.now(UTC)
    await session.flush()
    return len(trackers)
