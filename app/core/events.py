"""events.jsonl writer.

Append-only, line-terminator sentinel, monotonic event_seq per deployment.
fsync on stage boundaries and task_end with payload.task_name =
'playbook_on_stats'. Reader lives in the same module (Task 10).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Sentinel: empty JSON string terminated by newline. Readers skip this
# final zero-length value and treat it as "file intact, no partial line".
SENTINEL = b'""\n'

_STAGE_BOUNDARY_TYPES = frozenset({"phase_transition", "state_transition", "attempt_start", "attempt_end"})


class EventsWriter:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = self._recover_seq()

    def _recover_seq(self) -> int:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0
        last = 0
        with self.path.open("rb") as fh:
            for raw in fh:
                s = raw.strip()
                if not s or s == b'""':
                    continue
                try:
                    obj = json.loads(s)
                except ValueError:
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("event_seq"), int):
                    last = max(last, obj["event_seq"])
        return last

    def _should_fsync(self, event: dict[str, Any]) -> bool:
        et = event.get("event_type")
        if et in _STAGE_BOUNDARY_TYPES:
            return True
        if et == "task_end":
            payload = event.get("payload") or {}
            if payload.get("task_name") == "playbook_on_stats":
                return True
        return False

    def append(self, event: dict[str, Any], *, attempt_id: str,
               deployment_id: str | None = None) -> int:
        with self._lock:
            self._seq += 1
            stamped = dict(event)
            stamped["event_seq"] = self._seq
            stamped["attempt_id"] = attempt_id
            if deployment_id is not None:
                stamped["deployment_id"] = deployment_id
            stamped.setdefault("ts",
                               datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
            line = json.dumps(stamped, separators=(",", ":")).encode("utf-8") + b"\n"
            needs_fsync = self._should_fsync(stamped)
            # Append body + sentinel. The sentinel is rewritten every time so
            # the tail always ends with '""\n' regardless of crash point.
            # Readers and _recover_seq skip empty '""' lines.
            with self.path.open("ab") as fh:
                fh.write(line)
                fh.write(SENTINEL)
                fh.flush()
                if needs_fsync:
                    os.fsync(fh.fileno())
            return self._seq

    @property
    def current_seq(self) -> int:
        return self._seq


import asyncio  # noqa: E402
from typing import AsyncIterator, Iterator  # noqa: E402


class EventsReader:
    """Synchronous seq-indexed reader over events.jsonl."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read_range(self, *, from_seq: int = 0,
                   to_seq: int | None = None) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("rb") as fh:
            for raw in fh:
                s = raw.strip()
                if not s or s == b'""':
                    continue
                try:
                    obj = json.loads(s)
                except ValueError:
                    # partial trailing line
                    continue
                seq = obj.get("event_seq")
                if not isinstance(seq, int) or seq < from_seq:
                    continue
                if to_seq is not None and seq > to_seq:
                    break
                yield obj


async def tail_events(path: Path, *, from_seq: int = 0,
                      stop: asyncio.Event | None = None,
                      poll_ms: int = 250) -> AsyncIterator[dict[str, Any]]:
    """Async generator that yields events with event_seq > from_seq.

    Uses watchfiles on Linux when available, else falls back to polling.
    Skips the last line if it parses as a partial JSON (see SENTINEL).
    """
    path = Path(path)
    last = from_seq - 1
    stop = stop or asyncio.Event()

    def _read_from(threshold: int) -> list[dict[str, Any]]:
        r = EventsReader(path)
        return [e for e in r.read_range(from_seq=threshold + 1)]

    try:
        from watchfiles import awatch  # type: ignore
        use_watch = True
    except Exception:
        use_watch = False

    # Initial drain.
    for ev in _read_from(last):
        last = ev["event_seq"]
        yield ev

    if use_watch:
        # awatch(parent_dir) yields change batches; each batch triggers a
        # re-scan. Wrap __anext__ in a task so asyncio.wait() timeouts don't
        # cancel the underlying coroutine (cancellation would poison the
        # async generator). Falls back to polling interval as an idle tick
        # so the stop event is honoured even when no fs events arrive.
        watcher = awatch(path.parent, stop_event=stop)
        pending_task: asyncio.Task | None = None
        try:
            while not stop.is_set():
                if pending_task is None:
                    pending_task = asyncio.create_task(watcher.__anext__())
                done, _ = await asyncio.wait(
                    {pending_task}, timeout=poll_ms / 1000,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if pending_task in done:
                    try:
                        pending_task.result()
                    except StopAsyncIteration:
                        pass
                    pending_task = None
                for ev in _read_from(last):
                    last = ev["event_seq"]
                    yield ev
        finally:
            if pending_task is not None and not pending_task.done():
                pending_task.cancel()
                try:
                    await pending_task
                except (asyncio.CancelledError, StopAsyncIteration, Exception):
                    pass
    else:
        while not stop.is_set():
            await asyncio.sleep(poll_ms / 1000)
            for ev in _read_from(last):
                last = ev["event_seq"]
                yield ev


class EventsIdx:
    """Sidecar index mapping event_seq -> byte_offset.

    Deferred per spec §13 — stub raises NotImplementedError. Current
    runtime uses the line-count cursor in EventsReader/EventsWriter.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def build(self) -> None:
        raise NotImplementedError("events.idx deferred per spec §13")

    def lookup(self, event_seq: int) -> int:
        raise NotImplementedError("events.idx deferred per spec §13")
