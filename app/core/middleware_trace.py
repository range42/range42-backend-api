"""ASGI middleware that injects/echoes X-Range42-Trace-Id and binds it
to the structlog contextvars for the duration of the request.
"""
from __future__ import annotations

import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
import structlog

HEADER = "X-Range42-Trace-Id"


class TraceIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request, call_next):
        incoming = request.headers.get(HEADER) or uuid.uuid4().hex
        # Expose on request.state so exception handlers can retrieve it even
        # when the client did not send the header (generated id case).
        request.state.trace_id = incoming
        tokens = structlog.contextvars.bind_contextvars(trace_id=incoming)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.reset_contextvars(**tokens)
        response.headers[HEADER] = incoming
        return response
