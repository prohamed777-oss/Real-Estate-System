"""Handler registry for events and jobs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

EVENT_HANDLERS: dict[str, list[Callable[..., Awaitable[None]]]] = defaultdict(list)
JOB_HANDLERS: dict[str, Callable[..., Awaitable[None]]] = {}

F = TypeVar("F", bound=Callable[..., Awaitable[None]])


def event_handler(event_name: str) -> Callable[[F], F]:
    """Register an async handler for a domain event.

    Handler signature: async def h(session, envelope: dict) -> None
    Runs inside the dispatcher's per-event savepoint; raising an exception
    triggers retry with backoff, then dead-letter.
    """

    def deco(fn: F) -> F:
        EVENT_HANDLERS[event_name].append(fn)
        return fn

    return deco


def job_handler(job_type: str) -> Callable[[F], F]:
    """Register an async job executor.

    Signature: async def h(session, tenant_id, payload) -> None
    """

    def deco(fn: F) -> F:
        if job_type in JOB_HANDLERS:
            raise ValueError(f"Duplicate job handler: {job_type}")
        JOB_HANDLERS[job_type] = fn
        return fn

    return deco


def register_all() -> None:
    """Import all modules that register handlers/jobs (idempotent)."""
    from app import bootstrap  # noqa: F401  (imports hook up all domains)
