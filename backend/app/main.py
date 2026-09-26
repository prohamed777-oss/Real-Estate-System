"""Real Estate Revenue OS — FastAPI application factory."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine
from app.core.errors import DomainError

log = logging.getLogger("revenue_os")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting %s (env=%s)", settings.app_name, settings.app_env)
    # Seed global agent profiles (idempotent) — ONE runtime, many profiles
    from app.ai.agents import seed_default_profiles
    from app.core.db import session_factory

    try:
        async with session_factory() as session:
            async with session.begin():
                await seed_default_profiles(session)
    except Exception as exc:  # noqa: BLE001 — DB may not be migrated yet on first boot
        log.warning("profile seeding skipped: %s", exc)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    from app import bootstrap  # noqa: F401 — registers all event/job handlers
    from app.core.security import get_auth  # noqa: F401
    from app.identity.api import router as identity_router
    from app.organizations.api import router as org_router
    from app.organizations.auth_api import router as auth_router

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- request id / trace middleware ----
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        trace_id = request.headers.get("X-Trace-ID") or request_id
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # ---- error envelope ----
    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {"code": "validation_failed", "message": "Invalid request", "details": exc.errors()},
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "internal_error", "message": "Internal server error", "details": {}},
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    # ---- health ----
    @app.get("/health")
    async def health() -> dict:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return {"status": "ok", "env": settings.app_env}
        except Exception as exc:  # noqa: BLE001
            return {"status": "degraded", "error": str(exc)[:200]}

    # ---- internal cron endpoints (Vercel Cron hits these) ----
    from app.jobs.api import router as jobs_router

    app.include_router(jobs_router)

    prefix = settings.api_prefix
    app.include_router(auth_router, prefix=prefix)
    app.include_router(org_router, prefix=prefix)
    app.include_router(identity_router, prefix=prefix)

    # ---- domain routers (registered by each milestone) ----
    from app.bootstrap import include_domain_routers

    include_domain_routers(app, prefix)

    return app


app = create_app()
