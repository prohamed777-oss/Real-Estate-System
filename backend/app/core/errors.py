"""Unified error model.

Every error the API returns follows one envelope:
    {"error": {"code": ..., "message": ..., "details": ...}, "request_id": ...}

Domain code raises DomainError subclasses; the API layer never invents ad-hoc errors.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for all business-rule violations."""

    status_code = 400
    code = "domain_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class PermissionDenied(DomainError):
    status_code = 403
    code = "permission_denied"


class NotAuthenticated(DomainError):
    status_code = 401
    code = "not_authenticated"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class ValidationFailed(DomainError):
    status_code = 422
    code = "validation_failed"


class IdempotencyConflict(DomainError):
    status_code = 409
    code = "idempotency_conflict"


class TenantContextMissing(DomainError):
    status_code = 400
    code = "tenant_context_missing"


class ExternalProviderError(DomainError):
    status_code = 502
    code = "external_provider_error"
