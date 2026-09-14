"""Short-lived, application-local catalog metadata snapshots; never checkouts/PATs."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from time import monotonic

from starlette.requests import Request

from app.core.models import Source, SourceRepo
from app.schemas.v1.catalog import CatalogEntrySummary


def repository_key(source: Source, repo: SourceRepo) -> str:
    """Credential changes and cross-worker refresh timestamps select new snapshots."""
    identity = [source.id, source.created_at, source.provider, source.base_url,
                source.auth_kind, hashlib.sha256((source.token_ref or "").encode()).hexdigest(),
                repo.id, repo.owner, repo.repo, repo.branch, repo.manifest_path, repo.last_refreshed_at]
    return hashlib.sha256(json.dumps(identity, default=str).encode()).hexdigest()


@dataclass
class _Snapshot:
    expires: float
    entries: list[CatalogEntrySummary]
    size: int


class CatalogSnapshots:
    def __init__(self, *, ttl_seconds: float = 30, max_entries: int = 32,
                 max_bytes: int = 16 * 1024 * 1024, concurrency: int = 4,
                 clock: Callable[[], float] = monotonic):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.clock = clock
        self._snapshots: OrderedDict[str, _Snapshot] = OrderedDict()
        self._pending: dict[tuple[int, str], asyncio.Task[list[CatalogEntrySummary]]] = {}
        self._slots = asyncio.Semaphore(concurrency)
        self._generation = 0

    def invalidate(self) -> None:
        # Do not cancel workers: each owns its temporary checkout until cleanup.
        # Work begun before an edit/refresh may finish, but cannot refill the cache.
        self._generation += 1
        self._snapshots.clear()

    async def get(self, key: str, loader: Callable[[], list[CatalogEntrySummary]]) -> list[CatalogEntrySummary]:
        now = self.clock()
        for expired in [name for name, snapshot in self._snapshots.items() if snapshot.expires <= now]:
            del self._snapshots[expired]
        cached = self._snapshots.get(key)
        if cached is not None:
            self._snapshots.move_to_end(key)
            return deepcopy(cached.entries)
        pending_key = (self._generation, key)
        task = self._pending.get(pending_key)
        if task is None:
            task = asyncio.create_task(self._load(key, self._generation, loader))
            self._pending[pending_key] = task
            task.add_done_callback(lambda done: self._finished(pending_key, done))
        # One disconnected browser must not cancel a clone shared by other reads.
        return deepcopy(await asyncio.shield(task))

    def _finished(self, key: tuple[int, str], task: asyncio.Task[list[CatalogEntrySummary]]) -> None:
        self._pending.pop(key, None)
        if not task.cancelled():
            task.exception()  # Retrieve failures even when every caller disconnected.

    async def _load(self, key: str, generation: int,
                    loader: Callable[[], list[CatalogEntrySummary]]) -> list[CatalogEntrySummary]:
        async with self._slots:
            entries = await asyncio.to_thread(loader)
        size = sum(len(entry.model_dump_json().encode()) for entry in entries)
        if generation == self._generation and size <= self.max_bytes:
            self._snapshots[key] = _Snapshot(self.clock() + self.ttl_seconds, deepcopy(entries), size)
            while len(self._snapshots) > self.max_entries or sum(item.size for item in self._snapshots.values()) > self.max_bytes:
                self._snapshots.popitem(last=False)
        return entries


def catalog_snapshots(request: Request) -> CatalogSnapshots:
    if not hasattr(request.app.state, "catalog_snapshots"):
        request.app.state.catalog_snapshots = CatalogSnapshots()
    return request.app.state.catalog_snapshots
