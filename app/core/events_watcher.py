"""Translate ansible-runner job_events/*.json into Range42 events.jsonl.

Runs the redaction pipeline on every event before append. Used by the
detached runner path and the orphan-adopt path.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.core.events import EventsReader, EventsWriter
from app.core.redaction import (
    RedactionAuditWriter, RedactionLayer, run_pipeline,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


# Ansible callback events -> Range42 SSE types (spec §18.2). This table only
# covers the events ansible-runner produces. The other 5 event types in the
# §18.2 vocabulary are emitted elsewhere:
#   - state_transition: emitted by the state-machine transitions in
#     app/core/state_machine.py on every DB-backed state write.
#   - redaction: emitted by app/core/redaction.py pipeline after any layer
#     fires (audit-sanitised; never carries redacted content).
#   - heartbeat: emitted by the sse-starlette heartbeat task in
#     app/routes/v1/deployments/events.py at 15s idle intervals.
#   - proxmox_task: emitted by app/core/proxmox_task_observer.py whenever a
#     long-running Proxmox task UUID is observed in ansible output (regex on
#     runner_on_ok.res.changed_when_matches "UPID:").
#   - preflight_check: emitted by app/core/preflight.py per-check, written
#     directly to events.jsonl (preflight is not routed through the runner).
_ANSIBLE_TO_RANGE42 = {
    # The deploy trigger owns lifecycle events and their durable terminal state.
    "playbook_on_start": "log_line",
    "playbook_on_stats": "log_line",
    "playbook_on_play_start": "phase_transition",
    "runner_on_ok": "task_end",
    "runner_on_failed": "task_end",
    "runner_on_unreachable": "host_unreachable",
    "runner_on_skipped": "task_end",
    "runner_on_start": "task_start",
}


def _translate(ansible_event: dict[str, Any]) -> dict[str, Any]:
    et = ansible_event.get("event", "log_line")
    data = ansible_event.get("event_data") or {}
    r42_type = _ANSIBLE_TO_RANGE42.get(et, "log_line")
    payload: dict[str, Any] = {}
    if r42_type in ("task_start", "task_end"):
        payload["task_name"] = data.get("task") or data.get("task_action") or "unknown"
        payload["host"] = data.get("host")
        if et == "runner_on_ok":
            payload["result"] = "ok"
        elif et == "runner_on_failed":
            payload["result"] = "failed"
        elif et == "runner_on_skipped":
            payload["result"] = "skipped"
        payload["res"] = data.get("res") or {}
    elif r42_type == "phase_transition":
        payload["to"] = data.get("name")
    elif r42_type == "host_unreachable":
        payload["host"] = data.get("host")
        # Preserve an actionable diagnosis without copying SSH stderr, which
        # can include credentials, commands or private workspace paths.
        result = data.get("res")
        message = result.get("msg") if isinstance(result, dict) and not result.get("_ansible_no_log") else ""
        message = message.casefold() if isinstance(message, str) else ""
        if "host key verification failed" in message or "remote host identification has changed" in message:
            payload.update(code="SSH_HOST_KEY_REJECTED", detail=(
                "SSH rejected the host key. Verify the node or guest key independently "
                "before updating the workspace known_hosts file."
            ))
        elif "permission denied" in message and "publickey" in message:
            payload.update(code="SSH_AUTHENTICATION_FAILED", detail=(
                "SSH authentication failed. Check the target user and workspace SSH credentials."
            ))
        else:
            payload.update(code="HOST_UNREACHABLE", detail=(
                "The host could not be reached. Check the target address, network access and SSH credentials."
            ))
    else:
        payload["text"] = ansible_event.get("stdout") or data.get("stdout") or ""
        payload["ansible_event"] = et
        payload["task_action"] = data.get("task_action")
    return {"event_type": r42_type, "payload": payload,
            "proxmox_ts": ansible_event.get("created")}


class EventsWatcher:
    def __init__(self, *, job_events_dir: Path, writer: EventsWriter,
                 audit: RedactionAuditWriter, layers: list[RedactionLayer],
                 deployment_id: str, attempt_id: str,
                 stop: asyncio.Event | None = None, poll_ms: int = 250,
                 on_progress: Callable[[int], Awaitable[None]] | None = None) -> None:
        self.job_events_dir = Path(job_events_dir)
        self.writer = writer
        self.audit = audit
        self.layers = layers
        self.deployment_id = deployment_id
        self.attempt_id = attempt_id
        self.stop = stop or asyncio.Event()
        self.poll_ms = poll_ms
        self.on_progress = on_progress
        self._seen: set[str] = set()
        self._cursor = 0
        self._reported_cursor = 0
        for event in EventsReader(writer.path).read_range():
            if event.get("attempt_id") != attempt_id:
                continue
            self._cursor = max(self._cursor, event["event_seq"])
            if isinstance(event.get("runner_event_id"), str):
                self._seen.add(event["runner_event_id"])

    async def run(self) -> None:
        self.job_events_dir.mkdir(parents=True, exist_ok=True)
        while True:
            stopped_before_scan = self.stop.is_set()
            # A final scan is required after the process exits; the last event
            # files can arrive between a polling scan and stop.set().
            for p in sorted(self.job_events_dir.glob("*.json"), key=lambda p: (
                int(p.name.split("-", 1)[0]) if p.name.split("-", 1)[0].isdigit() else 0,
                p.name,
            )):
                if p.name in self._seen:
                    continue
                try:
                    raw = json.loads(p.read_text())
                except (ValueError, OSError):
                    continue
                self._seen.add(p.name)
                ev = _translate(raw)
                redacted = run_pipeline(ev, self.layers, audit=self.audit,
                                         deployment_id=self.deployment_id,
                                         attempt_id=self.attempt_id)
                # Persist the source identity in the same append as the event;
                # a second observer after restart can drain without replay.
                redacted["runner_event_id"] = p.name
                if redacted["event_type"] == "log_line":
                    # Truncating first can leave a credential prefix that no
                    # longer matches the complete known secret.
                    redacted["payload"]["text"] = redacted["payload"]["text"][:4096]
                self._cursor = self.writer.append(redacted, attempt_id=self.attempt_id,
                                                  deployment_id=self.deployment_id)
            # One short transaction per changed batch, including persisted
            # events recovered after restart. Idle polls do not write the DB.
            if self.on_progress is not None and self._cursor > self._reported_cursor:
                await self.on_progress(self._cursor)
                self._reported_cursor = self._cursor
            if stopped_before_scan:
                break
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.poll_ms / 1000)
            except asyncio.TimeoutError:
                pass
        logger.info("events watcher stopped", dir=str(self.job_events_dir))
