"""Rate limiting (§98): two levels — platform and tenant.

Postgres-backed fixed-window counters (serverless-safe: every instance shares
the DB window). Redis swap later behind the same interface.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError


class RateLimitExceeded(DomainError):
    status_code = 429
    code = "rate_limited"


async def check_rate_limit(
    session: AsyncSession, *, key: str, limit: int, window_seconds: int = 60,
) -> dict[str, Any]:
    """Fixed-window counter. key example: 'tenant:<uuid>:send' or 'ip:<addr>:login'."""
    now = datetime.now(UTC)
    window_start = now.replace(second=0, microsecond=0)
    if window_seconds > 60:
        window_start = now.replace(minute=0, second=0, microsecond=0)
    stmt = text(
        """
        INSERT INTO rate_limit_counters (bucket_key, window_start, count)
        VALUES (:key, :ws, 1)
        ON CONFLICT (bucket_key, window_start)
        DO UPDATE SET count = rate_limit_counters.count + 1
        RETURNING count
        """
    )
    res = await session.execute(stmt, {"key": key, "ws": window_start})
    count = int(res.scalar_one())
    # opportunistic cleanup of stale windows (cheap, indexed)
    if count == 1:
        await session.execute(
            text("DELETE FROM rate_limit_counters WHERE window_start < :cutoff"),
            {"cutoff": datetime.now(UTC) - timedelta(hours=2)},
        )
    if count > limit:
        raise RateLimitExceeded(
            "Rate limit exceeded", details={"limit": limit, "window_seconds": window_seconds}
        )
    return {"allowed": True, "count": count, "limit": limit}
