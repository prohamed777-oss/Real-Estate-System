"""B2: Error tracking via Sentry. Activated by SENTRY_DSN env var."""

from __future__ import annotations

import logging

log = logging.getLogger("revenue_os.observability")


def init_sentry() -> None:
    dsn = __import__("app.core.config", fromlist=["settings"]).settings.sentry_dsn
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            traces_sample_rate=0.1,
            environment=__import__("app.core.config", fromlist=["settings"]).settings.app_env,
        )
        log.info("Sentry initialized")
    except ImportError:
        log.warning("sentry-sdk not installed — pip install sentry-sdk[fastapi]")
