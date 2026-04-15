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
