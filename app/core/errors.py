"""Canonical /v1 error envelope + exception classes.

Schema per spec §18.1:
  {error, message, code, details[], trace_id, timestamp}
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
import structlog


class Range42Error(Exception):
    status: int = 400
    error: str = "error"
    code: str = "ERROR"

    def __init__(
        self,
        *,
        error: str | None = None,
        code: str | None = None,
        status: int | None = None,
        message: str = "",
        details: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message or error or code or "Range42Error")
        if error is not None:
            self.error = error
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.message = message or self.error
        self.details = details or []


class VmidProtectedError(Range42Error):
    status = 409
    error = "vmid_protected"
    code = "VMID_PROTECTED"


class WorkspaceNonLocalFsError(Range42Error):
    status = 409
    error = "workspace_fs_invalid"
    code = "WORKSPACE_NON_LOCAL_FS"


class PreflightBlockedError(Range42Error):
    status = 400
    error = "preflight_blocked"
    code = "PREFLIGHT_BLOCKED"


class SourceUnreachableError(Range42Error):
    status = 502
    error = "source_unreachable"
    code = "GIT_UNREACHABLE"


class AuthFailedError(Range42Error):
    status = 502
    error = "proxmox_auth_failed"
    code = "AUTH_FAILED"


class RunnerSetupError(Range42Error):
    status = 500
    error = "runner_setup_error"
    code = "RUNNER_SETUP_ERROR"


class ProjectCheckoutError(Range42Error):
    status = 502
    error = "project_checkout_error"
    code = "PROJECT_CHECKOUT_FAILED"


def _trace_id(request: Request) -> str:
    """Resolve trace id with fallback chain:
    request.state (set by TraceIdMiddleware) -> incoming header -> "".
    """
    tid = getattr(request.state, "trace_id", None)
    if tid:
        return tid
    return request.headers.get("X-Range42-Trace-Id", "") or ""


def _envelope(err: Range42Error, trace_id: str) -> dict[str, Any]:
    return {
        "error": err.error,
        "message": err.message,
        "code": err.code,
        "details": err.details,
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    }


def install_exception_handlers(app: FastAPI) -> None:
    log = structlog.get_logger("http")

    @app.exception_handler(Range42Error)
    async def handle_range42(request: Request, exc: Range42Error):
        trace_id = _trace_id(request)
        log.warning(
            "range42_error",
            error=exc.error,
            code=exc.code,
            status=exc.status,
        )
        return JSONResponse(_envelope(exc, trace_id), status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException):
        trace_id = _trace_id(request)
        r = Range42Error(
            error="http_error",
            code=f"HTTP_{exc.status_code}",
            status=exc.status_code,
            message=str(exc.detail),
            details=[],
        )
        return JSONResponse(_envelope(r, trace_id), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        trace_id = _trace_id(request)
        details = [
            {
                "field": ".".join(str(x) for x in e.get("loc", ())),
                "reason": e.get("msg", "invalid"),
            }
            for e in exc.errors()
        ]
        r = Range42Error(
            error="validation_error",
            code="VALIDATION",
            status=422,
            message="Request validation failed",
            details=details,
        )
        return JSONResponse(_envelope(r, trace_id), status_code=422)
