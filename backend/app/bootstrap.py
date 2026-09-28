"""Import hook: registers ALL event/job handlers and exposes domain routers.

Domain modules self-register via `registry.event_handler` / `registry.job_handler`
at import time. `bootstrap` guarantees every handler module is loaded exactly once.
"""

from __future__ import annotations

import logging

log = logging.getLogger("revenue_os.bootstrap")

_loaded = False


HANDLER_MODULES = [
    "app.channels.handlers",
    "app.ai.handlers",
    "app.automation.handlers",
    "app.leads.handlers",
    "app.matching.handlers",
    "app.sales.handlers",
    "app.finance.handlers",
    "app.marketing.handlers",
    "app.signals.handlers",
    "app.signals.decision_handlers",
    # ops/maintenance handlers (retention etc.) must run in the runner process
    # too, not only in the API process — otherwise those jobs dead-letter.
    "app.jobs.ops_api",
]


def load_all() -> None:
    """Import every domain handler module. Missing modules warn loudly —
    by delivery time this list must import cleanly with zero warnings."""
    global _loaded
    if _loaded:
        return
    import importlib

    for module in HANDLER_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name and exc.name != module and not module.startswith(exc.name):
                raise  # a real dependency error inside an existing module
            log.warning("handler module not present yet: %s", module)
    _loaded = True
    log.info("domain handler registration complete")


load_all()


ROUTER_MODULES = [
    "app.leads.api",
    "app.conversations.api",
    "app.channels.api",
    "app.properties.api",
    "app.properties.reconciliation_api",
    "app.listings.api",
    "app.matching.api",
    "app.sales.api",
    "app.finance.api",
    "app.finance.documents_api",
    "app.marketing.api",
    "app.automation.api",
    "app.ai.api",
    "app.analytics.api",
    "app.analytics.billing_api",
    "app.importer.api",
    "app.capability.api",
]


def include_domain_routers(app, prefix: str) -> None:
    import importlib

    for module in ROUTER_MODULES:
        try:
            mod = importlib.import_module(module)
            app.include_router(mod.router, prefix=prefix)
        except ModuleNotFoundError as exc:
            if exc.name and exc.name != module and not module.startswith(exc.name):
                raise
            log.warning("router module not present yet: %s", module)
