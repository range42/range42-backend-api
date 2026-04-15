"""Protocol for invoking ansible-runner.

Two implementations:
  - RealRunner (Task 20): spawns `ansible-runner start` via asyncio.create_subprocess_exec.
  - FakeRunner (tests/fixtures): emits a canned event list synchronously.

Both produce a RunnerHandle with an async event stream, pid, rc, and kill().
"""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Protocol, Any


class RunnerHandle(Protocol):
    pid: int | None
    rc: int | None
    artifact_dir: Path

    def events(self) -> AsyncIterator[dict[str, Any]]: ...
    async def wait(self) -> int: ...
    async def kill(self) -> None: ...


class RunnerProtocol(Protocol):
    async def start(self, *, private_data_dir: Path,
                    extravars: dict[str, Any],
                    envvars: dict[str, str]) -> RunnerHandle: ...
