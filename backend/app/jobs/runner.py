"""Local jobs runner — long-running worker loop for development.

    python -m app.jobs.runner            # runs forever, ticks every 2s
    python -m app.jobs.runner --once     # single tick and exit

In production (Vercel), cron hits /internal/jobs/tick instead — same code path.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("revenue_os.runner")


async def tick() -> dict:
    from app import bootstrap  # registers ALL domain event/job handlers

    bootstrap.load_all()
    from app.jobs.api import _tick
    from app.core.db import session_factory

    async with session_factory() as session:
        async with session.begin():
            return await _tick(session)


async def loop(interval_seconds: float) -> None:
    log.info("jobs runner started (interval=%ss)", interval_seconds)
    while True:
        started = time.monotonic()
        try:
            stats = await tick()
            if any(stats.values()):
                log.info("tick: %s", stats)
        except Exception as exc:  # noqa: BLE001 — runner must survive DB hiccups
            log.error("tick failed: %s", exc)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.5, interval_seconds - elapsed))


def main() -> None:
    parser = argparse.ArgumentParser(description="Revenue OS jobs runner")
    parser.add_argument("--once", action="store_true", help="run a single tick")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    os.environ.setdefault("APP_ENV", "development")
    if args.once:
        stats = asyncio.run(tick())
        print(stats)
    else:
        asyncio.run(loop(args.interval))


if __name__ == "__main__":
    main()
