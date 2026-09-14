"""Application-wide bearer authentication, including streaming requests."""
from __future__ import annotations

import hmac
import hashlib
import logging
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import Match

from app.core.access import Principal, allowed
from app.core import audit
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
    if not token and settings.api_principals_file:
        return None
    if len(token) < 32 or any(char.isspace() for char in token):
        raise RuntimeError("RANGE42_API_TOKEN or RANGE42_API_TOKEN_FILE must provide a token of at least 32 characters without whitespace")
    return token


def _route_template(scope):
    application = scope.get("app")
    routes = getattr(application, "routes", [])
    partial = False
    for candidate in routes:
        match, _ = candidate.matches(scope)
        if match == Match.FULL:
            return getattr(candidate, "path_format", candidate.path)
        partial = partial or match == Match.PARTIAL
    # Preserve the router's canonical slash redirect for explicitly permitted
    # routes. Never normalize arbitrary paths or authorize unknown handlers.
    if not partial and scope["type"] == "http" and getattr(getattr(application, "router", None), "redirect_slashes", False):
        path = scope["path"]
        redirect_scope = {**scope, "path": path.rstrip("/") if path.endswith("/") else path + "/"}
        for candidate in routes:
            match, _ = candidate.matches(redirect_scope)
            if match == Match.FULL:
                return getattr(candidate, "path_format", candidate.path)
    return "<unmatched>"


class BearerAuthMiddleware:
    """Pure ASGI middleware keeps SSE streaming and disconnects intact.

    Only the non-sensitive liveness probe is public. Query parameters never
    carry credentials, avoiding their persistence in URLs and access logs.
    """

    def __init__(self, app: ASGIApp, *, token: str | None,
                 principals: dict[str, Principal] | None = None, audit_enabled: bool = False):
        self.app = app
        self.token = token
        self.principals = principals or {}
        self.audit_enabled = audit_enabled or bool(self.principals)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope["path"] == "/v1/health" and scope["method"] in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        principal = None
        value = Headers(scope=scope).get("authorization", "")
        scheme, _, provided = value.partition(" ")
        if self.token is None and not self.principals:
            principal = Principal("development", "admin")
        elif scheme.lower() == "bearer":
            if self.token and hmac.compare_digest(provided.encode(), self.token.encode()):
                principal = Principal("shared-operator", "admin")
            elif 32 <= len(provided) <= 4096 and not any(char.isspace() for char in provided):
                hashed = hashlib.sha256(provided.encode()).hexdigest()
                for expected, candidate in self.principals.items():
                    if hmac.compare_digest(hashed, expected):
                        principal = candidate
        if principal is None:
            await self._deny(scope, receive, send, 401, "AUTH_REQUIRED", "A valid backend bearer token is required")
            return
        state = scope.setdefault("state", {})
        state["principal"], state["audit_enabled"] = principal, self.audit_enabled
        route = _route_template(scope)
        method = scope.get("method", "WEBSOCKET")
        permitted = allowed(principal, method, route)
        record_id = None
        if self.audit_enabled and scope["type"] == "http" and method not in {"GET", "HEAD", "OPTIONS"}:
            try:
                record_id = await audit.begin_record(principal, method, route)
            except Exception:
                await self._deny(scope, receive, send, 503, "AUDIT_UNAVAILABLE", "Mutation audit storage is unavailable; no action was dispatched")
                return

        async def audited_send(message):
            if message["type"] == "http.response.start" and record_id:
                outcome = "completed"
                try:
                    await audit.finish_record(record_id, message["status"], denied=not permitted)
                except Exception:
                    # The mutation may already have executed. Preserve its HTTP
                    # result; leave the durable intent visibly unfinished.
                    outcome = "unconfirmed"
                    logging.getLogger(__name__).error("Mutation audit completion unavailable for record %s", record_id)
                message = {**message, "headers": [*message.get("headers", []),
                    (b"x-range42-audit-id", record_id.encode()), (b"x-range42-audit-state", outcome.encode())]}
            await send(message)

        if not permitted:
            await self._deny(scope, receive, audited_send, 403, "ACCESS_DENIED", "This backend identity does not have permission for this operation")
            return
        await self.app(scope, receive, audited_send)

    async def _deny(self, scope, receive, send, status, code, message):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401 if status == 401 else 4403})
            return
        error = Range42Error(error="authentication_required" if status == 401 else "access_denied", code=code, status=status, message=message)
        response = JSONResponse(_envelope(error, scope.get("state", {}).get("trace_id", "")), status_code=status,
                                headers={"WWW-Authenticate": "Bearer"} if status == 401 else {})
        await response(scope, receive, send)
