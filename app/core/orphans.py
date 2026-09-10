"""Reconcile orphaned ansible-runner subprocesses after FastAPI restart.

Database attempts are reconciled with their per-attempt runner artifacts.
Adoption verifies process identity; exit recovery uses the runner's recorded
return code and preserves cancellation. The legacy filesystem scanner below
is retained only as a read-only diagnostic helper.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from sqlalchemy import and_, or_, select

from app.core.config import settings
from app.core.db import get_session_factory
from app.core.models import Attempt, Deployment, WorkspaceLock
from app.core.attempt_lifecycle import TERMINAL_ATTEMPT_STATES, advance_attempt_cursor, finish_attempt, keep_attempt_lock
from app.core.runner_detached import process_matches, signal_running_attempt
from app.core.events import EventsWriter
from app.core.workspace import shred_envvars
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


_TASKS: dict[str, asyncio.Task] = {}


def track_attempt(attempt_id: str, task: asyncio.Task) -> None:
    _TASKS[attempt_id] = task
    task.add_done_callback(lambda done: untrack_attempt(attempt_id, done))


def untrack_attempt(attempt_id: str, task: asyncio.Task) -> None:
    if _TASKS.get(attempt_id) is task:
        _TASKS.pop(attempt_id, None)


async def stop_observers() -> None:
    tasks = list(_TASKS.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _read_rc(artifact: Path) -> int | None:
    try:
        return int((artifact / "rc").read_text()[:32].strip())
    except (OSError, ValueError):
        return None


def _watcher(dep: Deployment, attempt: Attempt, artifact: Path, stop: asyncio.Event):
    # Raw artifacts are only replayed when the original redaction context is
    # available. Credentials may have rotated in the database since the run.
    try:
        secrets = json.loads((artifact / "redaction.json").read_text())
        if not isinstance(secrets, list) or not all(isinstance(v, str) for v in secrets):
            return
    except (OSError, ValueError):
        return
    from app.core.events_watcher import EventsWatcher
    from app.core.redaction import ConfigDenylistLayer, RedactionAuditWriter, TaintedStringLayer, VaultTaggedLayer
    ws = Path(dep.workspace_path)
    return EventsWatcher(
        job_events_dir=artifact / "job_events", writer=EventsWriter(ws / "events.jsonl"),
        audit=RedactionAuditWriter(ws / "redactions.jsonl"),
        layers=[ConfigDenylistLayer(settings.redaction_denylist), VaultTaggedLayer(), TaintedStringLayer(set(secrets))],
        deployment_id=dep.id, attempt_id=attempt.id, stop=stop,
        on_progress=lambda cursor: advance_attempt_cursor(attempt_id=attempt.id, event_cursor_tip=cursor),
    )


async def _drain_events(dep: Deployment, attempt: Attempt, artifact: Path) -> None:
    stop = asyncio.Event()
    stop.set()
    watcher = _watcher(dep, attempt, artifact, stop)
    if watcher is not None:
        await watcher.run()


async def _observe(dep: Deployment, attempt: Attempt, artifact: Path, pid: int) -> None:
    stop = asyncio.Event()
    watcher = _watcher(dep, attempt, artifact, stop)
    tasks = [asyncio.create_task(keep_attempt_lock(
        attempt_id=attempt.id, deployment_id=dep.id, stop=stop,
    ))]
    if watcher is not None:
        tasks.append(asyncio.create_task(watcher.run()))
    try:
        while process_matches(artifact, pid):
            await asyncio.sleep(0.25)
        stop.set()
        await asyncio.gather(*tasks)
        await _finish_recovered(dep, attempt, artifact, _read_rc(artifact))
    finally:
        # Cancelling an adopted observer on API shutdown leaves the independently
        # running process and its credentials intact for the next API instance.
        stop.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _finish_recovered(dep: Deployment, attempt: Attempt, artifact: Path, rc: int | None) -> None:
    await _drain_events(dep, attempt, artifact)
    writer = EventsWriter(Path(dep.workspace_path) / "events.jsonl")
    runtime_result = {}
    if attempt.scope == "runtime":
        from app.core.runtime_completion import observe_runtime_completion
        runtime_result = await observe_runtime_completion(attempt.id, writer)
    state = await finish_attempt(
        attempt_id=attempt.id, rc=rc, unknown=rc is None,
        **({"error_code": "RUNNER_EXIT_UNOBSERVED"} if rc is None else runtime_result),
    )
    cursor = writer.append({"event_type": "attempt_end", "payload": {
        "terminal_state": state, "rc": rc, "recovered": True,
    }}, attempt_id=attempt.id, deployment_id=dep.id)
    await finish_attempt(attempt_id=attempt.id, rc=rc, event_cursor_tip=cursor)
    for relative in ("env/envvars", "env/extravars", "command", "redaction.json"):
        shred_envvars(artifact / relative)


async def reconcile_once() -> list[ReconcileResult]:
    """Reconcile database attempts against their own per-attempt artifacts."""
    async with get_session_factory()() as session:
        locked_cancel = select(WorkspaceLock.deployment_id).where(
            WorkspaceLock.deployment_id == Attempt.deployment_id,
            WorkspaceLock.owner == "attempt-" + Attempt.id,
        ).correlate(Attempt).exists()
        rows = (await session.execute(
            select(Attempt, Deployment).join(Deployment, Attempt.deployment_id == Deployment.id)
            .where(or_(Attempt.state.not_in(TERMINAL_ATTEMPT_STATES),
                       and_(Attempt.state == "cancelled", locked_cancel)))
        )).all()
    results = []
    for attempt, dep in rows:
        task = _TASKS.get(attempt.id)
        if task is not None and not task.done():
            continue
        artifact = Path(dep.workspace_path) / "runner" / attempt.id
        if attempt.artifact_dir and Path(attempt.artifact_dir).resolve() != artifact.resolve():
            logger.warning("invalid recovery artifact directory", attempt_id=attempt.id)
            continue
        if artifact.is_symlink() or not artifact.resolve().is_relative_to(Path(dep.workspace_path).resolve()):
            continue
        pid = attempt.pid or 0
        if not pid:
            try:
                pid = int((artifact / "pid").read_text())
            except (OSError, ValueError):
                # A crash during preparation can precede PID persistence. Give
                # a newly reserved request time to register its local task.
                started = attempt.started_at or dep.updated_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - started).total_seconds() < 90:
                    continue
        rc = _read_rc(artifact)
        alive = process_matches(artifact, pid)
        classification = "alive" if alive else "completed_unflushed" if rc is not None else "unknown"
        results.append(ReconcileResult(Path(dep.workspace_path), pid, classification, None))
        if alive:
            if attempt.state == "cancelled":
                await signal_running_attempt(dep.workspace_path)
            async with get_session_factory()() as session:
                lock = await session.get(WorkspaceLock, dep.id)
                if lock is None:
                    session.add(WorkspaceLock(deployment_id=dep.id, owner=f"attempt-{attempt.id}", heartbeat_interval_s=30))
                elif lock.owner != f"attempt-{attempt.id}":
                    logger.warning("orphan lock belongs to another attempt", attempt_id=attempt.id)
                    continue
                await session.commit()
            track_attempt(attempt.id, asyncio.create_task(_observe(dep, attempt, artifact, pid)))
        else:
            await _finish_recovered(dep, attempt, artifact, rc)
    return results
