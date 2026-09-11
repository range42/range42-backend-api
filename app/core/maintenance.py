"""Drain finite HTTP work before maintenance using a persistent admission flock."""
from __future__ import annotations

import asyncio
import fcntl
import os
from pathlib import Path
import re
import stat

from starlette.responses import JSONResponse

from app.core.runner_detached import _process_identity


PROTOCOL = "flock-http-intent-v2"
_EVENT_STREAM = re.compile(r"/v1/deployments/[^/]+/events")


class MaintenanceGate:
    def __init__(self, path: Path | None):
        self.path = path
        self._identity: dict | None = None
        self._tasks: set[asyncio.Task] = set()

    def _open(self) -> int:
        path = self.path
        if path is None or not path.is_absolute() or path != path.resolve():
            raise ValueError("Maintenance lock needs an absolute path without symbolic links")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
            raise ValueError("Maintenance lock parent must be private and owned by the API user")
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        if self._identity is None:
            flags |= os.O_CREAT
        descriptor = os.open(path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("Maintenance lock must be a private regular file owned by the API user")
            identity = {"path": str(path), "device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid}
            if self._identity is not None and self._identity != identity:
                raise ValueError("Maintenance lock identity changed; restore the original lock before restarting")
            self._identity = identity
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def capability(self) -> dict:
        if self.path is not None:
            os.close(self._open())
        process = _process_identity(os.getpid())
        if process is None:
            raise ValueError("Cannot verify the API process identity")
        return {"protocol": PROTOCOL, "enabled": self.path is not None,
                "process": process, "lock": self._identity}

    def acquire_shared(self) -> int | None:
        descriptor = self._open()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        try:
            # A crashed installer loses flock, but its intent survives on this
            # same persistent inode. Any nonempty value fails closed.
            if os.fstat(descriptor).st_size:
                os.close(descriptor)
                return None
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def track(self, task: asyncio.Task, descriptor: int) -> None:
        # The downstream task owns the descriptor. Cancelling a disconnected
        # caller cannot unlock a still-running sync/threadpool/background job.
        self._tasks.add(task)

        def finished(completed: asyncio.Task) -> None:
            os.close(descriptor)
            self._tasks.discard(completed)
            if not completed.cancelled():
                completed.exception()  # Retrieve errors even after caller disconnect.

        task.add_done_callback(finished)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.shield(asyncio.gather(*self._tasks, return_exceptions=True))
            # gather can return synchronously for done tasks; let their release
            # callbacks run before inspecting the active set again.
            await asyncio.sleep(0)


class MaintenanceMiddleware:
    def __init__(self, app, *, gate: MaintenanceGate):
        self.app = app
        self.gate = gate

    async def __call__(self, scope, receive, send):
        safe_get = (scope.get("method") == "GET" and
                    (scope["path"] == "/v1/health" or _EVENT_STREAM.fullmatch(scope["path"]) is not None))
        bypass = scope["type"] != "http" or self.gate.path is None or safe_get
        if bypass:
            await self.app(scope, receive, send)
            return
        try:
            descriptor = self.gate.acquire_shared()
        except (OSError, ValueError):
            response = JSONResponse({"code": "MAINTENANCE_GATE_UNAVAILABLE", "error": "maintenance_gate_unavailable",
                                     "message": "The backend maintenance lock is unavailable; restore its configured ownership and identity."},
                                    status_code=503, headers={"Retry-After": "5"})
            await response(scope, receive, send)
            return
        if descriptor is None:
            response = JSONResponse({"code": "MAINTENANCE_ACTIVE", "error": "maintenance_active",
                                     "message": "Backend maintenance is in progress. Retry after maintenance finishes."},
                                    status_code=503, headers={"Retry-After": "5"})
            await response(scope, receive, send)
            return
        task = asyncio.create_task(self.app(scope, receive, send))
        self.gate.track(task, descriptor)
        await asyncio.shield(task)
