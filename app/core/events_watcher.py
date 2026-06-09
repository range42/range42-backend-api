"""Translate ansible-runner job_events/*.json into Range42 events.jsonl.

Runs the redaction pipeline on every event before append. Used by the
detached runner path and the orphan-adopt path.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.events import EventsWriter
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
    "playbook_on_start": "attempt_start",
    "playbook_on_stats": "attempt_end",
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
    else:
        payload["text"] = (data.get("stdout") or "")[:4096]
    return {"event_type": r42_type, "payload": payload,
            "proxmox_ts": ansible_event.get("created")}


class EventsWatcher:
    def __init__(self, *, job_events_dir: Path, writer: EventsWriter,
                 audit: RedactionAuditWriter, layers: list[RedactionLayer],
                 deployment_id: str, attempt_id: str,
                 stop: asyncio.Event | None = None, poll_ms: int = 250) -> None:
        self.job_events_dir = Path(job_events_dir)
        self.writer = writer
        self.audit = audit
        self.layers = layers
        self.deployment_id = deployment_id
        self.attempt_id = attempt_id
        self.stop = stop or asyncio.Event()
        self.poll_ms = poll_ms
        self._seen: set[str] = set()

    async def run(self) -> None:
        self.job_events_dir.mkdir(parents=True, exist_ok=True)
        while not self.stop.is_set():
            for p in sorted(self.job_events_dir.glob("*.json")):
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
                self.writer.append(redacted, attempt_id=self.attempt_id,
                                   deployment_id=self.deployment_id)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.poll_ms / 1000)
            except asyncio.TimeoutError:
                pass
        logger.info("events watcher stopped", dir=str(self.job_events_dir))
