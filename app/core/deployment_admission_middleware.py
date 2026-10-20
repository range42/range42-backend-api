"""Legacy administrative writers have no trustworthy per-run scope.

Fence them for the whole request and refuse them while any retained run is
closed. Use v1 deployment operations for independently managed concurrent runs.
"""

from starlette.responses import JSONResponse

from app.core.config import Settings
from app.core.db import get_session_factory
from app.core.deployment_admission import _closed_scopes, refused
from app.core.errors import Range42Error, _envelope
from app.core.locks import ProvisioningLock


class LegacyWriteAdmissionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not scope["path"].startswith("/v0/")
            or scope["method"] in {"GET", "HEAD", "OPTIONS"}
        ):
            return await self.app(scope, receive, send)
        try:
            with ProvisioningLock(Settings().workspace_root / ".locks"):
                async with get_session_factory()() as session:
                    if await _closed_scopes(session):
                        raise refused("DEPLOYMENT_WRITES_PAUSED")
                    from app.core.capacity_ledger import assert_unscoped_writes_accepted
                    await assert_unscoped_writes_accepted(session)
                return await self.app(scope, receive, send)
        except Range42Error as error:
            response = JSONResponse(
                _envelope(error, scope.get("state", {}).get("trace_id", "")),
                status_code=error.status,
            )
            return await response(scope, receive, send)
