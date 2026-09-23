from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, AsyncIterator


class _FakeHandle:
    def __init__(self, script: list[dict[str, Any]], artifact_dir: Path) -> None:
        self._script = list(script)
        self.artifact_dir = artifact_dir
        self.pid = os.getpid()
        self.rc: int | None = 0

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        for ev in self._script:
            await asyncio.sleep(0)
            yield ev

    async def wait(self) -> int:
        return 0

    async def kill(self) -> None:
        self.rc = 130


class FakeRunner:
    def __init__(self, script: list[dict[str, Any]] | None = None,
                 rc: int = 0, burst_per_second: int | None = None) -> None:
        self.script = script or []
        self.rc = rc
        self.burst_per_second = burst_per_second

    async def start(self, *, private_data_dir: Path,
                    extravars: dict[str, Any], envvars: dict[str, str]) -> _FakeHandle:
        private_data_dir.mkdir(parents=True, exist_ok=True)
        h = _FakeHandle(self.script, private_data_dir)
        h.rc = self.rc
        return h
