"""Reconcile orphaned ansible-runner subprocesses after FastAPI restart.

Scans workspace_root for '*/runner/pid' files. For each:
  - Alive (kill -0 succeeds): adopt as observer (EventsWatcher reattaches).
  - Dead + last event in events.jsonl older than 60s: mark 'unknown'.
  - Dead + last event younger than 60s: likely completed-but-unflushed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ReconcileResult:
    deployment_dir: Path
    pid: int
    classification: Literal["alive", "unknown", "completed_unflushed"]
    last_event_age_s: float | None


def classify_pid(pid: int, *, last_event_age_s: float | None) -> str:
    alive = True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        alive = False
    except PermissionError:
        alive = True
    if alive:
        return "alive"
    if last_event_age_s is None or last_event_age_s >= 60:
        return "unknown"
    return "completed_unflushed"


def _last_event_age_seconds(events_path: Path) -> float | None:
    if not events_path.exists():
        return None
    last_ts: str | None = None
    with events_path.open("rb") as fh:
        for raw in fh:
            s = raw.strip()
            if not s or s == b'""':
                continue
            try:
                obj = json.loads(s)
            except ValueError:
                continue
            ts = obj.get("ts")
            if isinstance(ts, str):
                last_ts = ts
    if not last_ts:
        return None
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - t).total_seconds())


def scan_workspaces(workspace_root: Path | None = None) -> Iterator[ReconcileResult]:
    root = Path(workspace_root or settings.workspace_root)
    if not root.exists():
        return
    for pid_file in root.glob("*/runner/pid"):
        try:
            pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            continue
        dep_dir = pid_file.parent.parent
        age = _last_event_age_seconds(dep_dir / "events.jsonl")
        cls = classify_pid(pid, last_event_age_s=age)
        yield ReconcileResult(deployment_dir=dep_dir, pid=pid,
                              classification=cls, last_event_age_s=age)


async def reconcile_once() -> list[ReconcileResult]:
    """Run orphan classification + act on each result.

    alive               -> attach a live EventsWatcher to the workspace's
                          events.jsonl tail so SSE subscribers see new
                          events from the resumed subprocess.
    unknown             -> update the attempt row's state to 'unknown' so
                          the UI surfaces the crash-mid-run state
                          distinctly from failed.
    completed_unflushed -> log only; the final playbook_on_stats event
                          likely landed and flush-at-exit completed the
                          state machine.

    Downstream adopt/mark actions (EventsWatcher.attach_observer and
    state_machine.mark_attempt_unknown) land in follow-up tasks. Until
    those modules exist, this function still scans and logs every
    classification result so operators see orphans in the structured log.
    """
    results = list(scan_workspaces())
    for r in results:
        logger.info("orphan scan result",
                    deployment_dir=str(r.deployment_dir),
                    pid=r.pid, classification=r.classification,
                    last_event_age_s=r.last_event_age_s)
        if r.classification == "alive":
            try:
                from app.core.events_watcher import EventsWatcher  # noqa: F401
                attach = getattr(EventsWatcher, "attach_observer", None)
                if attach is not None:
                    await attach(r.deployment_dir)
            except Exception as exc:  # noqa: BLE001
                logger.warning("orphan adopt skipped",
                               deployment_dir=str(r.deployment_dir),
                               error=str(exc))
        elif r.classification == "unknown":
            try:
                from app.core.state_machine import mark_attempt_unknown
                await mark_attempt_unknown(r.deployment_dir)
            except Exception as exc:  # noqa: BLE001
                logger.warning("orphan unknown-mark skipped",
                               deployment_dir=str(r.deployment_dir),
                               error=str(exc))
    return results
