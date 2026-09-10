"""Application-wide bearer authentication, including streaming requests."""
from __future__ import annotations

import hmac
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.core.errors import Range42Error, _envelope


def configured_api_token(settings: Settings) -> str | None:
    if settings.auth_mode not in {"required", "development"}:
        raise RuntimeError("RANGE42_AUTH_MODE must be required or development")
    if settings.auth_mode == "development":
        return None
    if settings.api_token and settings.api_token_file:
        raise RuntimeError("Configure only one of RANGE42_API_TOKEN or RANGE42_API_TOKEN_FILE")
    token = settings.api_token
    if settings.api_token_file:
        try:
            token = Path(settings.api_token_file).read_text().strip()
        except OSError as exc:
            raise RuntimeError("Cannot read RANGE42_API_TOKEN_FILE") from exc
    if len(token) < 32 or any(char.isspace() for char in token):
        raise RuntimeError("RANGE42_API_TOKEN or RANGE42_API_TOKEN_FILE must provide a token of at least 32 characters without whitespace")
    return token


class BearerAuthMiddleware:
    """Pure ASGI middleware keeps SSE streaming and disconnects intact.

    Only the non-sensitive liveness probe is public. Query parameters never
    carry credentials, avoiding their persistence in URLs and access logs.
    """

    def __init__(self, app: ASGIApp, *, token: str | None):
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or self.token is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope["path"] == "/v1/health" and scope["method"] in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        value = Headers(scope=scope).get("authorization", "")
        scheme, _, provided = value.partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(provided.encode(), self.token.encode()):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        error = Range42Error(error="authentication_required", code="AUTH_REQUIRED", status=401,
                             message="A valid backend bearer token is required")
        trace_id = scope.get("state", {}).get("trace_id", "")
        response = JSONResponse(_envelope(error, trace_id), status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        await response(scope, receive, send)
